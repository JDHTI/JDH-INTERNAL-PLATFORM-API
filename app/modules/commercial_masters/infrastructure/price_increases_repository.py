"""PostgreSQL repository of the price increase history."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.errors import NotFoundError
from app.infrastructure.database import Database
from app.modules.commercial_masters.domain.price_increase_analysis import AnalysisRow
from app.modules.commercial_masters.domain.price_increases import (
    VALUE_FIELDS,
    PriceIncrease,
    PriceIncreaseFilters,
    PriceIncreasePage,
    PriceIncreaseValues,
)
from app.modules.commercial_masters.infrastructure.models import (
    CommercialSupplierModel,
    PriceIncreaseModel,
    ProductModel,
)

P = PriceIncreaseModel
_CHUNK = 1000
_FIELDS = ("sku", *VALUE_FIELDS)


def _values(row: Any) -> PriceIncreaseValues:
    return PriceIncreaseValues(**{name: getattr(row, name) for name in _FIELDS})


class SqlAlchemyPriceIncreaseRepository:
    """Rows keyed by ``row_key``; an upload inserts or updates them in chunks."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def existing(self, keys: Sequence[str]) -> dict[str, PriceIncreaseValues]:
        """The stored values of these keys."""

        found: dict[str, PriceIncreaseValues] = {}
        async with self.database.session_factory() as session:
            for start in range(0, len(keys), _CHUNK):
                chunk = list(keys[start : start + _CHUNK])
                rows = await session.execute(select(P).where(P.row_key.in_(chunk)))
                for (model,) in rows:
                    found[model.row_key] = _values(model)
        return found

    async def known_skus(self, skus: Sequence[str]) -> set[str]:
        """The references the product master has."""

        known: set[str] = set()
        async with self.database.session_factory() as session:
            for start in range(0, len(skus), _CHUNK):
                chunk = list(skus[start : start + _CHUNK])
                rows = await session.scalars(
                    select(ProductModel.sku).where(ProductModel.sku.in_(chunk))
                )
                known.update(rows)
        return known

    async def upsert(self, rows: Sequence[PriceIncreaseValues], actor: str, now: datetime) -> None:
        """Insert new keys and overwrite the values of existing ones, in one transaction."""

        if not rows:
            return
        records = [
            {
                "id": uuid4(),
                "row_key": row.key,
                "updated_by": actor,
                "updated_at": now,
                **asdict(row),
            }
            for row in rows
        ]
        statement = insert(P)
        statement = statement.on_conflict_do_update(
            index_elements=[P.row_key],
            set_={
                name: statement.excluded[name]
                for name in (*VALUE_FIELDS, "sku", "updated_by", "updated_at")
            },
        )
        async with self.database.session_factory() as session, session.begin():
            for start in range(0, len(records), _CHUNK):
                await session.execute(statement, records[start : start + _CHUNK])

    def _query(self, filters: PriceIncreaseFilters):
        conditions = []
        if filters.sku:
            conditions.append(P.sku == filters.sku.strip())
        if filters.status:
            conditions.append(P.status == filters.status)
        if filters.pending is True:
            conditions.append(P.system_updated_on.is_(None))
        elif filters.pending is False:
            conditions.append(P.system_updated_on.is_not(None))
        if filters.q and filters.q.strip():
            term = filters.q.strip()
            conditions.append(
                or_(
                    P.sku.startswith(term, autoescape=True),
                    ProductModel.description.icontains(term, autoescape=True),
                    CommercialSupplierModel.name.icontains(term, autoescape=True),
                )
            )
        joined = P.__table__.outerjoin(ProductModel, ProductModel.sku == P.sku).outerjoin(
            CommercialSupplierModel,
            CommercialSupplierModel.code == ProductModel.commercial_supplier_code,
        )
        return joined, and_(*conditions) if conditions else None

    async def page(self, filters: PriceIncreaseFilters, page: int, size: int) -> PriceIncreasePage:
        """The rows of the filter, the latest increase first, with the counts by status."""

        joined, where = self._query(filters)
        columns = (
            P,
            ProductModel.description,
            CommercialSupplierModel.name,
            ProductModel.category,
            ProductModel.id,
        )
        listing = select(*columns).select_from(joined)
        counting = select(
            func.count(),
            *(func.count(case((P.status == status, 1))) for status in ("subio", "bajo", "estable")),
            func.count(case((P.system_updated_on.is_(None), 1))),
        ).select_from(joined)
        if where is not None:
            listing = listing.where(where)
            counting = counting.where(where)
        listing = (
            listing.order_by(
                P.increased_on.desc().nulls_last(),
                P.notified_on.desc().nulls_last(),
                P.sku,
                P.id,
            )
            .offset((page - 1) * size)
            .limit(size)
        )
        async with self.database.session_factory() as session:
            total, up, down, flat, pending = (await session.execute(counting)).one()
            rows = (await session.execute(listing)).all()
        items = tuple(
            PriceIncrease(
                id=model.id,
                values=_values(model),
                updated_by=model.updated_by,
                updated_at=model.updated_at,
                description=description,
                supplier_name=supplier,
                category=category,
                is_known_product=product_id is not None,
            )
            for model, description, supplier, category, product_id in rows
        )
        counts = {"subio": up, "bajo": down, "estable": flat, "pendientes": pending}
        return PriceIncreasePage(items, int(total), page, size, counts)

    async def version(self) -> tuple[int, datetime | None]:
        """Rows and last change: when either moves, the analysis is worked out again."""

        async with self.database.session_factory() as session:
            count, last = (
                await session.execute(select(func.count(), func.max(P.updated_at)))
            ).one()
        return int(count), last

    async def analysis_rows(self) -> list[AnalysisRow]:
        """Every dated row with only what the analysis reads, and the product's description,
        supplier and category. The whole history: the rows before a period give the cost it
        started at."""

        statement = (
            select(
                P.sku,
                P.increased_on,
                P.notified_on,
                P.status,
                P.variation_pct,
                P.old_cost,
                P.old_cost_with_tax,
                P.new_cost,
                P.new_cost_with_tax,
                P.system_updated_on.is_(None),
                ProductModel.description,
                CommercialSupplierModel.name,
                ProductModel.category,
            )
            .select_from(
                P.__table__.outerjoin(ProductModel, ProductModel.sku == P.sku).outerjoin(
                    CommercialSupplierModel,
                    CommercialSupplierModel.code == ProductModel.commercial_supplier_code,
                )
            )
            .where(P.increased_on.is_not(None))
        )
        async with self.database.session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [AnalysisRow(*row) for row in rows]

    async def set_system_date(
        self, increase_id: UUID, day: date | None, actor: str, now: datetime
    ) -> None:
        """Store when SIESA got the increase (``None`` clears it).

        Raises:
            NotFoundError: The row does not exist.
        """

        async with self.database.session_factory() as session, session.begin():
            result = await session.execute(
                update(P)
                .where(P.id == increase_id)
                .values(system_updated_on=day, updated_by=actor, updated_at=now)
            )
            if result.rowcount == 0:
                raise NotFoundError("Alza no encontrada")

    async def get(self, increase_id: UUID) -> PriceIncrease:
        """One row with its product's data.

        Raises:
            NotFoundError: The row does not exist.
        """

        page = await self._one(increase_id)
        if page is None:
            raise NotFoundError("Alza no encontrada")
        return page

    async def _one(self, increase_id: UUID) -> PriceIncrease | None:
        statement = (
            select(
                P,
                ProductModel.description,
                CommercialSupplierModel.name,
                ProductModel.category,
                ProductModel.id,
            )
            .select_from(
                P.__table__.outerjoin(ProductModel, ProductModel.sku == P.sku).outerjoin(
                    CommercialSupplierModel,
                    CommercialSupplierModel.code == ProductModel.commercial_supplier_code,
                )
            )
            .where(P.id == increase_id)
        )
        async with self.database.session_factory() as session:
            row = (await session.execute(statement)).first()
        if row is None:
            return None
        model, description, supplier, category, product_id = row
        return PriceIncrease(
            id=model.id,
            values=_values(model),
            updated_by=model.updated_by,
            updated_at=model.updated_at,
            description=description,
            supplier_name=supplier,
            category=category,
            is_known_product=product_id is not None,
        )


__all__ = ["SqlAlchemyPriceIncreaseRepository"]
