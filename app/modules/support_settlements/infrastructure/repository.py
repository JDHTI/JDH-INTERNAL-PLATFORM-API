"""Stored settlements in PostgreSQL."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.errors import ConflictError, NotFoundError
from app.infrastructure.database import Database
from app.modules.support_settlements.domain.entities import (
    CLOSED,
    DRAFT,
    NO_SUPPLIER,
    SETTLED,
    Settlement,
    SettlementResult,
)
from app.modules.support_settlements.infrastructure.models import (
    SupportSettlementItemModel,
    SupportSettlementModel,
    SupportSettlementReferenceModel,
    SupportSettlementSaleModel,
)

H = SupportSettlementModel
R = SupportSettlementReferenceModel
L = SupportSettlementSaleModel
I = SupportSettlementItemModel  # noqa: E741
ZERO = Decimal(0)
# Rows per INSERT: SQLAlchemy splits an executemany under asyncpg's parameter cap.
_CHUNK = 1000
_DIMENSIONS = {
    "regional": L.regional,
    "business_unit": L.business_unit,
    "salesperson": L.salesperson,
    "customer": L.nit,
}


def _supplier(column: Any, code: str) -> Any:
    """The supplier filter; :data:`NO_SUPPLIER` asks for the rows with no supplier code."""

    return column.is_(None) if code == NO_SUPPLIER else column == code


def _header(model: SupportSettlementModel) -> Settlement:
    return Settlement(
        id=model.id,
        period=model.period,
        status=model.status,
        amount=model.amount,
        flagged_amount=model.flagged_amount,
        net=model.net,
        cost=model.cost,
        sales_read=model.sales_read,
        supported_lines=model.supported_lines,
        suppliers=model.suppliers,
        calculated_at=model.calculated_at,
        calculated_by=model.calculated_by,
        closed_at=model.closed_at,
        closed_by=model.closed_by,
        reopened_at=model.reopened_at,
        reopened_by=model.reopened_by,
        version=model.version,
    )


def _rows(result: SettlementResult, settlement_id: UUID) -> tuple[list, list, list]:
    refs = [
        {
            "id": uuid4(),
            "settlement_id": settlement_id,
            "supplier_code": ref.supplier_code,
            "supplier_name": ref.supplier_name,
            "sku": ref.sku,
            "description": ref.description,
            "quantity": ref.quantity,
            "net": ref.net,
            "cost": ref.cost,
            "amount": ref.amount,
            "flagged_amount": ref.flagged_amount,
            "supported_lines": ref.supported_lines,
        }
        for ref in result.references
    ]
    sales, items = [], []
    for sale in result.sales:
        sale_id = uuid4()
        line, customer = sale.line, sale.customer
        sales.append(
            {
                "id": sale_id,
                "settlement_id": settlement_id,
                "line_key": line.line_key,
                "sale_date": line.sale_date,
                "document": line.document,
                "co": line.co,
                "co_name": line.co_name,
                "regional": line.regional,
                "business_unit": customer.business_unit if customer else None,
                "channel": customer.channel if customer else None,
                "city": customer.city if customer else None,
                "salesperson": line.salesperson,
                "nit": line.nit,
                "branch_id": line.branch_id,
                "customer_name": customer.name if customer else None,
                "business_group": customer.business_group if customer else None,
                "supplier_code": sale.supplier_code,
                "supplier_name": sale.supplier_name,
                "sku": line.sku,
                "description": sale.description,
                "warehouse": line.warehouse_name or line.warehouse_code,
                "is_return": line.is_return,
                "reason": line.reason,
                "quantity": line.quantity,
                "gross": line.gross,
                "net": line.net,
                "cost": line.cost,
                "base_unit_cost": sale.base_unit_cost,
                "cost_source": sale.cost_source,
                "amount": sale.amount,
                "flags": list(sale.flags),
            }
        )
        for item in sale.items:
            items.append(
                {
                    "id": uuid4(),
                    "settlement_id": settlement_id,
                    "sale_id": sale_id,
                    "support_id": item.support_id,
                    "support_version": item.support_version,
                    "kind": item.kind,
                    "calc_method": item.calc_method,
                    "status": item.status,
                    "supplier_value": item.supplier_value,
                    "value_unit": item.value_unit,
                    "valid_from": item.valid_from,
                    "valid_to": item.valid_to,
                    "negotiated_pct": item.negotiated_pct,
                    "settled_pct": item.settled_pct,
                    "unit_amount": item.unit_amount,
                    "amount": item.amount,
                    "detail": item.detail,
                }
            )
    return refs, sales, items


class SqlAlchemySettlementRepository:
    """Settlements, their references, lines and supports applied."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def get(self, period: str) -> Settlement | None:
        async with self.database.session_factory() as session:
            model = (
                await session.execute(select(H).where(H.period == period))
            ).scalar_one_or_none()
        return None if model is None else _header(model)

    async def list(self) -> list[Settlement]:
        async with self.database.session_factory() as session:
            models = (await session.execute(select(H).order_by(H.period.desc()))).scalars().all()
        return [_header(model) for model in models]

    async def replace(
        self,
        period: str,
        result: SettlementResult,
        actor: str,
        now: datetime,
        *,
        close: bool = False,
    ) -> Settlement:
        """Store the month's calculation in place of the previous one, in one transaction.

        Raises:
            ConflictError: The month is closed.
        """

        async with self.database.session_factory() as session, session.begin():
            await session.execute(
                pg_insert(H)
                .values(
                    id=uuid4(),
                    period=period,
                    status=DRAFT,
                    calculated_at=now,
                    calculated_by=actor,
                )
                .on_conflict_do_nothing(index_elements=[H.period])
            )
            model = (
                await session.execute(select(H).where(H.period == period).with_for_update())
            ).scalar_one()
            if model.status == CLOSED:
                raise ConflictError(f"La liquidación de {period} está cerrada")
            await session.execute(delete(R).where(R.settlement_id == model.id))
            await session.execute(delete(L).where(L.settlement_id == model.id))
            refs, sales, items = _rows(result, model.id)
            for table, rows in ((R, refs), (L, sales), (I, items)):
                for start in range(0, len(rows), _CHUNK):
                    await session.execute(insert(table), rows[start : start + _CHUNK])
            settled = result.sales
            model.amount = sum((sale.amount for sale in settled), ZERO)
            model.flagged_amount = sum(
                (item.amount for sale in settled for item in sale.items if item.status != SETTLED),
                ZERO,
            )
            model.net = sum((ref.net for ref in result.references), ZERO)
            model.cost = sum((ref.cost for ref in result.references), ZERO)
            model.sales_read = result.sales_read
            model.supported_lines = len(settled)
            model.suppliers = len({ref.supplier_code for ref in result.references})
            model.calculated_at = now
            model.calculated_by = actor
            model.version += 1
            if close:
                model.status = CLOSED
                model.closed_at = now
                model.closed_by = actor
            await session.flush()
            return _header(model)

    async def reopen(self, period: str, actor: str, now: datetime) -> Settlement:
        """Put a closed month back to draft.

        Raises:
            NotFoundError: No settlement for the month.
            ConflictError: The month is not closed.
        """

        async with self.database.session_factory() as session, session.begin():
            model = (
                await session.execute(select(H).where(H.period == period).with_for_update())
            ).scalar_one_or_none()
            if model is None:
                raise NotFoundError(f"No hay liquidación de {period}")
            if model.status != CLOSED:
                raise ConflictError(f"La liquidación de {period} no está cerrada")
            model.status = DRAFT
            model.reopened_at = now
            model.reopened_by = actor
            model.version += 1
            await session.flush()
            return _header(model)

    async def suppliers(self, settlement_id: UUID) -> list[Mapping[str, Any]]:
        """Totals per supplier, the largest amount first."""

        query = (
            select(
                R.supplier_code,
                func.max(R.supplier_name).label("supplier_name"),
                func.count().label("references"),
                func.sum(R.supported_lines).label("supported_lines"),
                func.sum(R.quantity).label("quantity"),
                func.sum(R.net).label("net"),
                func.sum(R.cost).label("cost"),
                func.sum(R.amount).label("amount"),
                func.sum(R.flagged_amount).label("flagged_amount"),
            )
            .where(R.settlement_id == settlement_id)
            .group_by(R.supplier_code)
            .order_by(func.sum(R.amount).desc())
        )
        async with self.database.session_factory() as session:
            return [dict(row._mapping) for row in (await session.execute(query)).all()]

    async def references(
        self, settlement_id: UUID, supplier_code: str | None
    ) -> list[Mapping[str, Any]]:
        """The supported references, of one supplier when given, the largest amount first."""

        query = select(R).where(R.settlement_id == settlement_id)
        if supplier_code:
            query = query.where(_supplier(R.supplier_code, supplier_code))
        query = query.order_by(R.amount.desc(), R.sku)
        async with self.database.session_factory() as session:
            models = (await session.execute(query)).scalars().all()
        return [
            {
                "supplier_code": model.supplier_code,
                "supplier_name": model.supplier_name,
                "sku": model.sku,
                "description": model.description,
                "quantity": model.quantity,
                "net": model.net,
                "cost": model.cost,
                "amount": model.amount,
                "flagged_amount": model.flagged_amount,
                "supported_lines": model.supported_lines,
            }
            for model in models
        ]

    async def _items(self, session: Any, sale_ids: Sequence[UUID]) -> dict[UUID, list]:
        found: dict[UUID, list] = defaultdict(list)
        for start in range(0, len(sale_ids), _CHUNK):
            rows = (
                await session.execute(
                    select(I).where(I.sale_id.in_(list(sale_ids[start : start + _CHUNK])))
                )
            ).scalars()
            for item in rows:
                found[item.sale_id].append(
                    {
                        "support_id": item.support_id,
                        "support_version": item.support_version,
                        "kind": item.kind,
                        "calc_method": item.calc_method,
                        "status": item.status,
                        "supplier_value": item.supplier_value,
                        "value_unit": item.value_unit,
                        "valid_from": item.valid_from,
                        "valid_to": item.valid_to,
                        "negotiated_pct": item.negotiated_pct,
                        "settled_pct": item.settled_pct,
                        "unit_amount": item.unit_amount,
                        "amount": item.amount,
                        "detail": item.detail,
                    }
                )
        return found

    @staticmethod
    def _sale(model: SupportSettlementSaleModel) -> dict[str, Any]:
        return {
            column.name: getattr(model, column.name)
            for column in L.__table__.columns
            if column.name not in ("id", "settlement_id")
        } | {"id": model.id}

    def _filtered(self, settlement_id, supplier_code, sku, nit, status):
        query = select(L).where(L.settlement_id == settlement_id)
        if supplier_code:
            query = query.where(_supplier(L.supplier_code, supplier_code))
        if sku:
            query = query.where(L.sku == sku)
        if nit:
            query = query.where(L.nit == nit)
        if status:
            query = query.where(select(I.id).where(I.sale_id == L.id, I.status == status).exists())
        return query

    async def lines(
        self,
        settlement_id: UUID,
        *,
        supplier_code: str | None,
        sku: str | None,
        nit: str | None,
        status: str | None,
        page: int,
        size: int,
    ) -> tuple[list[Mapping[str, Any]], int]:
        """Sale lines with their supports, by date then document."""

        query = self._filtered(settlement_id, supplier_code, sku, nit, status)
        async with self.database.session_factory() as session:
            total = (
                await session.execute(select(func.count()).select_from(query.subquery()))
            ).scalar_one()
            models = (
                (
                    await session.execute(
                        query.order_by(L.sale_date, L.document, L.sku, L.id)
                        .offset((page - 1) * size)
                        .limit(size)
                    )
                )
                .scalars()
                .all()
            )
            items = await self._items(session, [model.id for model in models])
        return [self._sale(model) | {"items": items.get(model.id, [])} for model in models], int(
            total
        )

    async def breakdown(
        self, settlement_id: UUID, by: str, supplier_code: str | None
    ) -> list[Mapping[str, Any]]:
        """The supported lines' sales and supports by regional, business unit, salesperson or
        customer: where the supplier's money goes back."""

        key = _DIMENSIONS[by]
        name = func.max(L.customer_name) if by == "customer" else func.max(key)
        query = (
            select(
                key.label("key"),
                name.label("name"),
                func.count().label("lines"),
                func.sum(L.net).label("net"),
                func.sum(L.cost).label("cost"),
                func.sum(L.amount).label("amount"),
            )
            .where(L.settlement_id == settlement_id)
            .group_by(key)
            .order_by(func.sum(L.amount).desc())
        )
        if supplier_code:
            query = query.where(_supplier(L.supplier_code, supplier_code))
        async with self.database.session_factory() as session:
            return [dict(row._mapping) for row in (await session.execute(query)).all()]

    async def export_rows(
        self, settlement_id: UUID, supplier_code: str | None
    ) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
        """Every supported line (with its supports) and the references, for the workbook."""

        query = self._filtered(settlement_id, supplier_code, None, None, None).order_by(
            L.supplier_code, L.sku, L.sale_date, L.document
        )
        async with self.database.session_factory() as session:
            models = (await session.execute(query)).scalars().all()
            items = await self._items(session, [model.id for model in models])
        sales = [self._sale(model) | {"items": items.get(model.id, [])} for model in models]
        return sales, await self.references(settlement_id, supplier_code)


__all__ = ["SqlAlchemySettlementRepository"]
