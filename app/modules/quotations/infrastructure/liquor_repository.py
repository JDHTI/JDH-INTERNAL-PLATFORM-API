"""PostgreSQL repository of the liquor tax bulk changes."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update

from app.core.errors import ConflictError, NotFoundError
from app.infrastructure.database import Database
from app.modules.quotations.domain.liquor import LiquorTaxChange, LiquorTaxUpdate
from app.modules.quotations.infrastructure.models import LiquorTaxUpdateModel

M = LiquorTaxUpdateModel


def _update(model: LiquorTaxUpdateModel) -> LiquorTaxUpdate:
    return LiquorTaxUpdate(
        id=model.id,
        status=model.status,
        changes=tuple(LiquorTaxChange.from_dict(item) for item in model.rows or ()),
        export_rows=tuple(model.export_rows or ()),
        note=model.note,
        review_note=model.review_note,
        activation_date=model.activation_date,
        created_by=model.created_by,
        created_at=model.created_at,
        validated_by=model.validated_by,
        validated_at=model.validated_at,
        uploaded_by=model.uploaded_by,
        uploaded_at=model.uploaded_at,
        version=model.version,
    )


def _values(item: LiquorTaxUpdate) -> dict[str, object]:
    return {
        "status": item.status,
        "rows": [change.as_dict() for change in item.changes],
        "export_rows": list(item.export_rows),
        "note": item.note,
        "review_note": item.review_note,
        "activation_date": item.activation_date,
        "created_by": item.created_by,
        "created_at": item.created_at,
        "validated_by": item.validated_by,
        "validated_at": item.validated_at,
        "uploaded_by": item.uploaded_by,
        "uploaded_at": item.uploaded_at,
        "version": item.version,
    }


class SqlAlchemyLiquorTaxRepository:
    """Liquor tax changes with optimistic locking on ``version``."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def create(self, item: LiquorTaxUpdate) -> LiquorTaxUpdate:
        async with self.database.session_factory() as session, session.begin():
            session.add(M(id=item.id, **_values(item)))
        return item

    async def get(self, update_id: UUID) -> LiquorTaxUpdate | None:
        async with self.database.session_factory() as session:
            model = await session.get(M, update_id)
        return _update(model) if model is not None else None

    async def list(self, limit: int = 50) -> list[LiquorTaxUpdate]:
        async with self.database.session_factory() as session:
            rows = (
                await session.scalars(select(M).order_by(M.created_at.desc()).limit(limit))
            ).all()
        return [_update(row) for row in rows]

    async def save(self, item: LiquorTaxUpdate, expected_version: int) -> LiquorTaxUpdate:
        """Write the change if nobody changed it meanwhile.

        Raises:
            NotFoundError: It does not exist.
            ConflictError: Its version is not ``expected_version`` any more.
        """

        async with self.database.session_factory() as session, session.begin():
            result = await session.execute(
                update(M)
                .where(M.id == item.id, M.version == expected_version)
                .values(**_values(item))
            )
            if result.rowcount == 0:
                if await session.get(M, item.id) is None:
                    raise NotFoundError("Cambio de impuesto no encontrado")
                raise ConflictError("El cambio de impuesto cambió desde que lo abriste; recárgalo")
        return item


__all__ = ["SqlAlchemyLiquorTaxRepository"]
