"""PostgreSQL repository of the internal support requests."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select

from app.core.errors import ConflictError, NotFoundError
from app.infrastructure.database import Database
from app.modules.supplier_supports.domain.entities import Trace
from app.modules.supplier_supports.domain.internal_requests import (
    APPROVED,
    PENDING,
    REJECTED,
    Approval,
    InternalRequest,
    NewRequest,
    RequestItem,
    RequestRef,
)
from app.modules.supplier_supports.infrastructure.models import (
    InternalSupportRequestItemModel,
    InternalSupportRequestModel,
)
from app.modules.supplier_supports.infrastructure.publication import queue_supports
from app.modules.supplier_supports.infrastructure.repository import new_support_model

R = InternalSupportRequestModel
I = InternalSupportRequestItemModel  # noqa: E741


def _item(model: InternalSupportRequestItemModel) -> RequestItem:
    return RequestItem(
        sku=model.sku,
        requested_margin_pct=model.requested_margin_pct,
        description=model.description,
        unit=model.unit,
        cost=model.cost,
        quoted_price=model.quoted_price,
        margin_pct=model.margin_pct,
        min_margin_pct=model.min_margin_pct,
        approved_margin_pct=model.approved_margin_pct,
        support_id=model.support_id,
        target_margin_pct=model.target_margin_pct,
        base_support_id=model.base_support_id,
    )


def _request(
    model: InternalSupportRequestModel, items: list[InternalSupportRequestItemModel]
) -> InternalRequest:
    return InternalRequest(
        id=model.id,
        number=model.number,
        status=model.status,
        decision=model.decision,
        quotation_id=model.quotation_id,
        quotation_label=model.quotation_label,
        customer_nit=model.customer_nit,
        customer_name=model.customer_name,
        commercial_supplier_id=model.commercial_supplier_id,
        supplier_code=model.supplier_code,
        supplier_name=model.supplier_name,
        requested_for=model.requested_for,
        agreed_with=model.agreed_with,
        reason=model.reason,
        requested_by_id=model.requested_by_id,
        requested_by=model.requested_by,
        created_at=model.created_at,
        version=model.version,
        valid_from=model.valid_from,
        valid_to=model.valid_to,
        decided_by=model.decided_by,
        decided_at=model.decided_at,
        decision_reason=model.decision_reason,
        items=tuple(_item(item) for item in sorted(items, key=lambda value: value.position)),
        kind=model.kind,
    )


class SqlAlchemyInternalRequestRepository:
    """Requests and their items, one short transaction per operation."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    @staticmethod
    async def _items(session: Any, ids: list[UUID]) -> dict[UUID, list[Any]]:
        grouped: dict[UUID, list[Any]] = {}
        if not ids:
            return grouped
        rows = await session.execute(select(I).where(I.request_id.in_(ids)))
        for item in rows.scalars():
            grouped.setdefault(item.request_id, []).append(item)
        return grouped

    async def create(
        self, request: NewRequest, actor_id: UUID, actor: str, now: datetime
    ) -> InternalRequest:
        """Store a request; a SKU already pending for the same quotation (or, asked from the
        panel, for the same customer) is a conflict."""

        skus = [item.sku for item in request.items]
        from_quotation = request.quotation_id is not None
        scope = (
            R.quotation_id == request.quotation_id
            if from_quotation
            else and_(R.quotation_id.is_(None), R.customer_nit == request.customer_nit)
        )
        lock_key = str(request.quotation_id) if from_quotation else f"nit:{request.customer_nit}"
        async with self.database.session_factory() as session, session.begin():
            # One pending request per SKU and quotation (or customer): two requests of the same
            # scope are written one after the other (transaction lock on it).
            await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(lock_key))))
            pending = (
                await session.execute(
                    select(I.sku)
                    .join(R, R.id == I.request_id)
                    .where(scope, R.status == PENDING, I.sku.in_(skus))
                )
            ).scalars()
            repeated = sorted(set(pending))
            if repeated:
                raise ConflictError(
                    "Ya hay una solicitud pendiente para "
                    + ", ".join(repeated)
                    + (" en esta cotización" if from_quotation else " para este cliente")
                )
            model = R(
                id=uuid4(),
                status=PENDING,
                kind=request.kind,
                quotation_id=request.quotation_id,
                quotation_label=request.quotation_label,
                customer_nit=request.customer_nit,
                customer_name=request.customer_name,
                commercial_supplier_id=request.commercial_supplier_id,
                supplier_code=request.supplier_code,
                supplier_name=request.supplier_name,
                requested_for=request.requested_for,
                agreed_with=request.agreed_with,
                reason=request.reason,
                requested_by_id=actor_id,
                requested_by=actor,
                created_at=now,
                version=1,
            )
            session.add(model)
            await session.flush()
            items = [
                I(
                    id=uuid4(),
                    request_id=model.id,
                    position=index,
                    sku=item.sku,
                    description=item.description,
                    unit=item.unit,
                    cost=item.cost,
                    quoted_price=item.quoted_price,
                    margin_pct=item.margin_pct,
                    min_margin_pct=item.min_margin_pct,
                    requested_margin_pct=item.requested_margin_pct,
                    target_margin_pct=item.target_margin_pct,
                    base_support_id=item.base_support_id,
                )
                for index, item in enumerate(request.items)
            ]
            session.add_all(items)
            await session.flush()
            await session.refresh(model, attribute_names=["number"])
            return _request(model, items)

    async def get(self, request_id: UUID) -> InternalRequest | None:
        """One request with its items."""

        async with self.database.session_factory() as session:
            model = await session.get(R, request_id)
            if model is None:
                return None
            return _request(model, (await self._items(session, [model.id])).get(model.id, []))

    async def search(
        self,
        *,
        status: str | None,
        q: str | None,
        owner_id: UUID | None,
        page: int,
        size: int,
    ) -> tuple[list[InternalRequest], int]:
        """A page of requests, newest first."""

        conditions = []
        if status:
            conditions.append(R.status == status)
        if owner_id is not None:
            conditions.append(R.requested_by_id == owner_id)
        if q:
            term = q.strip()
            matches = [
                R.customer_nit.icontains(term, autoescape=True),
                R.customer_name.icontains(term, autoescape=True),
                R.supplier_code.icontains(term, autoescape=True),
                R.supplier_name.icontains(term, autoescape=True),
                R.requested_by.icontains(term, autoescape=True),
                R.id.in_(select(I.request_id).where(I.sku == term)),
            ]
            if term.lstrip("#").isdigit():
                matches.append(R.number == int(term.lstrip("#")))
            conditions.append(or_(*matches))
        async with self.database.session_factory() as session:
            total = (
                await session.execute(select(func.count()).select_from(R).where(*conditions))
            ).scalar_one()
            models = list(
                (
                    await session.execute(
                        select(R)
                        .where(*conditions)
                        .order_by(R.created_at.desc(), R.number.desc())
                        .offset((page - 1) * size)
                        .limit(size)
                    )
                ).scalars()
            )
            items = await self._items(session, [model.id for model in models])
        return [_request(model, items.get(model.id, [])) for model in models], int(total)

    async def _locked(self, session: Any, request_id: UUID, version: int) -> Any:
        model = (
            await session.execute(select(R).where(R.id == request_id).with_for_update())
        ).scalar_one_or_none()
        if model is None:
            raise NotFoundError("Solicitud no encontrada")
        if model.version != version:
            raise ConflictError("La solicitud cambió; recárgala")
        if model.status != PENDING:
            raise ConflictError("La solicitud ya fue resuelta")
        return model

    async def approve(
        self, request_id: UUID, version: int, approval: Approval, actor: str, now: datetime
    ) -> InternalRequest:
        """Approve and create one support per approved SKU, published to the DWH."""

        async with self.database.session_factory() as session, session.begin():
            model = await self._locked(session, request_id, version)
            items = (await self._items(session, [model.id])).get(model.id, [])
            trace = Trace("solicitud_interna")
            created: dict[str, Any] = {}
            for sku, data in approval.supports.items():
                if approval.margins.get(sku) is None:
                    continue
                support = new_support_model(uuid4(), data, actor, now, trace)
                support.internal_request_id = model.id
                session.add(support)
                created[sku] = support
            # The supports exist before the items point at them (no ORM relationship orders it).
            await session.flush()
            for item in items:
                item.approved_margin_pct = approval.margins.get(item.sku)
                support = created.get(item.sku)
                item.support_id = support.id if support is not None else None
            supports = list(created.values())
            await session.flush()
            model.status = APPROVED
            model.decision = approval.decision
            model.valid_from = approval.valid_from
            model.valid_to = approval.valid_to
            model.decided_by = actor
            model.decided_at = now
            model.decision_reason = approval.note
            model.version += 1
            await session.flush()
            await queue_supports(session, supports)
            return _request(model, items)

    async def reject(
        self, request_id: UUID, version: int, reason: str, actor: str, now: datetime
    ) -> InternalRequest:
        """Reject with a reason; nothing is created."""

        async with self.database.session_factory() as session, session.begin():
            model = await self._locked(session, request_id, version)
            model.status = REJECTED
            model.decided_by = actor
            model.decided_at = now
            model.decision_reason = reason
            model.version += 1
            await session.flush()
            items = (await self._items(session, [model.id])).get(model.id, [])
            return _request(model, items)

    async def latest_for_quotation(self, quotation_id: UUID) -> dict[str, RequestRef]:
        """The latest request of each SKU of a quotation."""

        async with self.database.session_factory() as session:
            rows = (
                await session.execute(
                    select(I.sku, R.id, R.status, R.decision, I.approved_margin_pct)
                    .join(R, R.id == I.request_id)
                    .where(R.quotation_id == quotation_id)
                    .distinct(I.sku)
                    .order_by(I.sku, R.created_at.desc(), R.number.desc())
                )
            ).all()
        return {
            sku: RequestRef(request_id, status, decision, approved)
            for sku, request_id, status, decision, approved in rows
        }


__all__ = ["SqlAlchemyInternalRequestRepository"]
