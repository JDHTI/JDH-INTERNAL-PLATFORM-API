"""PostgreSQL repository of the suppliers' increase requests."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from sqlalchemy import case, func, insert, select, update

from app.core.errors import NotFoundError
from app.infrastructure.database import Database
from app.modules.costs.domain.increases import (
    REQUEST_STATUSES,
    IncreaseRequest,
    IncreaseRequestPage,
    RequestLine,
    with_coverage,
)
from app.modules.costs.infrastructure.models import (
    CostIncreaseLineModel,
    CostIncreaseRequestModel,
)

Q = CostIncreaseRequestModel
L = CostIncreaseLineModel
_CHUNK = 1000
_LINE_FIELDS = (
    "row_number",
    "sku",
    "supplier_sku",
    "ean",
    "description",
    "pack_factor",
    "category",
    "tax_group",
    "icui_pct",
    "ibua",
    "old_cost",
    "old_cost_with_tax",
    "new_cost",
    "new_cost_with_tax",
    "variation_amount",
    "variation_pct",
    "change",
    "increased_on",
    "regular_discount_pct",
    "regional",
    "public_price",
    "observation",
    "accepted",
)


def _line_row(request_id: UUID, line: RequestLine) -> dict:
    return {
        "id": line.id,
        "request_id": request_id,
        **{name: getattr(line, name) for name in _LINE_FIELDS},
    }


def _line(model: CostIncreaseLineModel) -> RequestLine:
    return RequestLine(
        id=model.id,
        ledger_id=model.ledger_id,
        **{name: getattr(model, name) for name in _LINE_FIELDS},
    )


def _request(model: CostIncreaseRequestModel, counts: tuple[int, int], lines=()) -> IncreaseRequest:
    return IncreaseRequest(
        id=model.id,
        supplier_code=model.supplier_code,
        supplier_name=model.supplier_name,
        source=model.source,
        status=model.status,
        file_name=model.file_name,
        storage_key=model.storage_key,
        note=model.note,
        created_by=model.created_by,
        created_at=model.created_at,
        decided_by=model.decided_by,
        decided_at=model.decided_at,
        decision_note=model.decision_note,
        line_count=counts[0],
        accepted_count=counts[1],
        lines=tuple(lines),
    )


class SqlAlchemyIncreaseRepository:
    """Requests with their lines."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): The platform database.
        """

        self.database = database

    async def create(self, request: IncreaseRequest) -> None:
        """Store a request and its lines in one transaction."""

        lines = [_line_row(request.id, line) for line in request.lines]
        async with self.database.session_factory() as session, session.begin():
            await session.execute(
                insert(Q).values(
                    id=request.id,
                    supplier_code=request.supplier_code,
                    supplier_name=request.supplier_name,
                    source=request.source,
                    status=request.status,
                    file_name=request.file_name,
                    storage_key=request.storage_key,
                    note=request.note,
                    created_by=request.created_by,
                    created_at=request.created_at,
                    updated_at=request.created_at,
                )
            )
            for start in range(0, len(lines), _CHUNK):
                await session.execute(insert(L), lines[start : start + _CHUNK])

    def _counts(self):
        return (
            select(
                L.request_id,
                func.count().label("lines"),
                func.count(case((L.accepted.is_(True), 1))).label("accepted"),
            )
            .group_by(L.request_id)
            .subquery()
        )

    async def page(
        self, status: str | None, supplier: str | None, page: int, size: int
    ) -> IncreaseRequestPage:
        """The requests, the latest first, with the counts by status of the supplier filter.

        Args:
            status (str | None): ``en_revision``, ``aprobada`` or ``rechazada``.
            supplier (str | None): Commercial supplier code.
            page (int): Page, from 1.
            size (int): Rows per page.

        Returns:
            IncreaseRequestPage: The requests without their lines, and the counts.
        """

        counts = self._counts()
        listing = select(Q, counts.c.lines, counts.c.accepted).outerjoin(
            counts, counts.c.request_id == Q.id
        )
        counting = select(*(func.count(case((Q.status == value, 1))) for value in REQUEST_STATUSES))
        total = select(func.count()).select_from(Q)
        if supplier:
            listing = listing.where(Q.supplier_code == supplier)
            counting = counting.where(Q.supplier_code == supplier)
            total = total.where(Q.supplier_code == supplier)
        if status:
            listing = listing.where(Q.status == status)
            total = total.where(Q.status == status)
        listing = listing.order_by(Q.created_at.desc(), Q.id).offset((page - 1) * size).limit(size)
        async with self.database.session_factory() as session:
            rows = (await session.execute(listing)).all()
            by_status = (await session.execute(counting)).one()
            count = (await session.execute(total)).scalar_one()
        status_counts = dict(
            zip(REQUEST_STATUSES, (int(value) for value in by_status), strict=True)
        )
        return IncreaseRequestPage(
            items=tuple(
                _request(model, (lines or 0, accepted or 0)) for model, lines, accepted in rows
            ),
            total=int(count),
            page=page,
            size=size,
            counts=status_counts,
        )

    async def get(self, request_id: UUID, with_lines: bool = True) -> IncreaseRequest | None:
        """A request, with its lines (by sheet row, then reference) and the regionals each one
        covers, when asked."""

        async with self.database.session_factory() as session:
            model = await session.get(Q, request_id)
            if model is None:
                return None
            line_models = list(
                await session.scalars(
                    select(L)
                    .where(L.request_id == request_id)
                    .order_by(L.row_number.nulls_last(), L.sku, L.regional)
                )
            )
        counts = (len(line_models), sum(1 for line in line_models if line.accepted))
        lines = with_coverage([_line(line) for line in line_models]) if with_lines else []
        return _request(model, counts, lines)

    async def update_line(self, request_id: UUID, line: RequestLine, now: datetime) -> None:
        """Store a corrected line.

        Raises:
            NotFoundError: No such line in the request.
        """

        values = {name: getattr(line, name) for name in _LINE_FIELDS}
        async with self.database.session_factory() as session, session.begin():
            result = await session.execute(
                update(L).where(L.id == line.id, L.request_id == request_id).values(**values)
            )
            if result.rowcount == 0:
                raise NotFoundError("Línea no encontrada")
            await session.execute(update(Q).where(Q.id == request_id).values(updated_at=now))

    async def decide(
        self,
        request_id: UUID,
        status: str,
        actor: str,
        now: datetime,
        note: str | None,
        ledger_ids: Mapping[UUID, UUID],
    ) -> None:
        """Store the decision and, when approved, the history row each line became."""

        async with self.database.session_factory() as session, session.begin():
            await session.execute(
                update(Q)
                .where(Q.id == request_id)
                .values(
                    status=status,
                    decided_by=actor,
                    decided_at=now,
                    decision_note=note,
                    updated_at=now,
                )
            )
            rows = [
                {"id": line_id, "ledger_id": ledger_id} for line_id, ledger_id in ledger_ids.items()
            ]
            for start in range(0, len(rows), _CHUNK):
                await session.execute(update(L), rows[start : start + _CHUNK])


__all__ = ["SqlAlchemyIncreaseRepository"]
