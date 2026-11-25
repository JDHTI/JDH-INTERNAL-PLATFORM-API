"""PostgreSQL repository of the SIESA loads of pending increases."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import case, func, insert, select, text, update

from app.core.errors import ConflictError, NotFoundError
from app.infrastructure.database import Database
from app.modules.commercial_masters.domain.siesa_loads import (
    GENERATED,
    LOAD_STATUSES,
    LOADED,
    VOIDED,
    LoadPage,
    LoadRow,
    SiesaLoad,
)
from app.modules.commercial_masters.infrastructure.models import (
    PriceIncreaseLoadModel,
    PriceIncreaseLoadRowModel,
    PriceIncreaseModel,
)

L = PriceIncreaseLoadModel
R = PriceIncreaseLoadRowModel
P = PriceIncreaseModel
_CHUNK = 1000
# One load at a time is numbered and checked against the open ones.
_LOCK = text("SELECT pg_advisory_xact_lock(hashtext('price_increase_siesa_loads'))")


def _row(model: PriceIncreaseLoadRowModel) -> LoadRow:
    return LoadRow(
        increase_id=model.increase_id,
        sku=model.sku,
        description=model.description,
        nit=model.nit,
        branch=model.branch,
        currency=model.currency,
        unit=model.unit,
        price=model.price,
        activation_date=model.activation_date,
        delivery_days=model.delivery_days,
        increased_on=model.increased_on,
        other_nits=tuple(filter(None, (model.other_nits or "").split(","))),
    )


def _load(model: PriceIncreaseLoadModel, rows: Sequence[LoadRow] = ()) -> SiesaLoad:
    return SiesaLoad(
        id=model.id,
        number=model.number,
        status=model.status,
        row_count=model.row_count,
        created_by=model.created_by,
        created_at=model.created_at,
        confirmed_by=model.confirmed_by,
        confirmed_at=model.confirmed_at,
        loaded_on=model.loaded_on,
        voided_by=model.voided_by,
        voided_at=model.voided_at,
        rows=tuple(rows),
    )


def open_rows():
    """``(increase_id, number)`` of the increases in a load not yet confirmed nor voided."""

    return select(R.increase_id, L.number).join(L, L.id == R.load_id).where(L.status == GENERATED)


class SqlAlchemySiesaLoadRepository:
    """Loads with their rows; confirming one dates its increases in the history."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def open_loads(self, increase_ids: Sequence[UUID]) -> dict[UUID, int]:
        """Of these increases, those in an open load, with its number."""

        found: dict[UUID, int] = {}
        async with self.database.session_factory() as session:
            for start in range(0, len(increase_ids), _CHUNK):
                chunk = list(increase_ids[start : start + _CHUNK])
                rows = await session.execute(open_rows().where(R.increase_id.in_(chunk)))
                found.update({increase_id: number for increase_id, number in rows})
        return found

    async def create(self, rows: Sequence[LoadRow], actor: str, now: datetime) -> SiesaLoad:
        """Number and store a load, in one transaction.

        Raises:
            ConflictError: One of the increases went into another open load meanwhile.
        """

        load_id = uuid4()
        ids = [row.increase_id for row in rows]
        async with self.database.session_factory() as session, session.begin():
            await session.execute(_LOCK)
            taken = (
                await session.execute(open_rows().where(R.increase_id.in_(ids)).limit(1))
            ).first()
            if taken is not None:
                raise ConflictError(
                    f"Un alza de la selección ya está en el lote {taken[1]}; vuelve a generar"
                )
            number = (
                await session.execute(select(func.coalesce(func.max(L.number), 0)))
            ).scalar_one()
            await session.execute(
                insert(L).values(
                    id=load_id,
                    number=number + 1,
                    status=GENERATED,
                    row_count=len(rows),
                    created_by=actor,
                    created_at=now,
                )
            )
            records = [
                {
                    "id": uuid4(),
                    "load_id": load_id,
                    "increase_id": row.increase_id,
                    "position": position,
                    "sku": row.sku,
                    "description": row.description,
                    "nit": row.nit,
                    "branch": row.branch,
                    "currency": row.currency,
                    "unit": row.unit,
                    "price": row.price,
                    "activation_date": row.activation_date,
                    "delivery_days": row.delivery_days,
                    "increased_on": row.increased_on,
                    "other_nits": ",".join(row.other_nits) or None,
                }
                for position, row in enumerate(rows, start=1)
            ]
            for start in range(0, len(records), _CHUNK):
                await session.execute(insert(R), records[start : start + _CHUNK])
        found = await self.get(load_id)
        assert found is not None
        return found

    async def get(self, load_id: UUID) -> SiesaLoad | None:
        """A load with its rows in file order."""

        async with self.database.session_factory() as session:
            model = await session.get(L, load_id)
            if model is None:
                return None
            rows = list(
                await session.scalars(select(R).where(R.load_id == load_id).order_by(R.position))
            )
        return _load(model, [_row(row) for row in rows])

    async def page(self, status: str | None, page: int, size: int) -> LoadPage:
        """The loads, the latest first, with the counts by status."""

        listing = select(L)
        total = select(func.count()).select_from(L)
        if status:
            listing = listing.where(L.status == status)
            total = total.where(L.status == status)
        listing = listing.order_by(L.number.desc()).offset((page - 1) * size).limit(size)
        counting = select(*(func.count(case((L.status == value, 1))) for value in LOAD_STATUSES))
        async with self.database.session_factory() as session:
            models = list(await session.scalars(listing))
            count = (await session.execute(total)).scalar_one()
            by_status = (await session.execute(counting)).one()
        return LoadPage(
            items=tuple(_load(model) for model in models),
            total=int(count),
            page=page,
            size=size,
            counts=dict(zip(LOAD_STATUSES, (int(value) for value in by_status), strict=True)),
        )

    async def _decide(self, session, load_id: UUID) -> PriceIncreaseLoadModel:
        model = (
            await session.execute(select(L).where(L.id == load_id).with_for_update())
        ).scalar_one_or_none()
        if model is None:
            raise NotFoundError("Lote no encontrado")
        if model.status != GENERATED:
            raise ConflictError("El lote ya fue confirmado o anulado")
        return model

    async def confirm(self, load_id: UUID, loaded_on: date, actor: str, now: datetime) -> None:
        """Mark the load as loaded in SIESA and date its increases.

        Raises:
            NotFoundError: No such load.
            ConflictError: It was already confirmed or voided.
        """

        async with self.database.session_factory() as session, session.begin():
            model = await self._decide(session, load_id)
            ids = select(R.increase_id).where(R.load_id == load_id).scalar_subquery()
            await session.execute(
                update(P)
                .where(P.id.in_(ids))
                .values(
                    system_updated_on=loaded_on,
                    siesa_changed=True,
                    updated_by=actor,
                    updated_at=now,
                )
            )
            model.status = LOADED
            model.confirmed_by = actor
            model.confirmed_at = now
            model.loaded_on = loaded_on

    async def void(self, load_id: UUID, actor: str, now: datetime) -> None:
        """Void a load not yet confirmed: its increases are free again.

        Raises:
            NotFoundError: No such load.
            ConflictError: It was already confirmed or voided.
        """

        async with self.database.session_factory() as session, session.begin():
            model = await self._decide(session, load_id)
            model.status = VOIDED
            model.voided_by = actor
            model.voided_at = now


__all__ = ["SqlAlchemySiesaLoadRepository", "open_rows"]
