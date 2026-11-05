"""PostgreSQL repository for the master declarations (the records live in the DWH)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.infrastructure.database import Database
from app.modules.masters.domain.entities import MasterDetail, MasterField, MasterSummary
from app.modules.masters.infrastructure.models import MasterDefinitionModel, MasterFieldModel


def _summary(model: MasterDefinitionModel) -> MasterSummary:
    return MasterSummary(
        id=model.id,
        slug=model.slug,
        name=model.name,
        description=model.description,
        source_system=model.source_system,
        source_object=model.source_object,
        grain=model.grain,
        natural_key=list(model.natural_key),
        record_count=None,
        last_synced_at=None,
        dwh_sequence=model.dwh_sequence,
    )


def _field(model: MasterFieldModel) -> MasterField:
    return MasterField(
        name=model.name,
        label=model.label,
        data_type=model.data_type,
        role=model.role,
        is_nullable=model.is_nullable,
        is_pii=model.is_pii,
        references=model.references_slug,
    )


class SqlAlchemyMasterRepository:
    """Read the active masters and their fields with one short-lived session."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def list_definitions(self) -> list[MasterDetail]:
        """Active masters with their fields, in sort order.

        Returns:
            list[MasterDetail]: Without counts; the service reads them from the DWH.
        """

        return await self._definitions(None)

    async def get_definition(self, slug: str) -> MasterDetail | None:
        """One active master with its fields.

        Args:
            slug (str): Master slug.

        Returns:
            MasterDetail | None: The master, or ``None`` when it is unknown or retired.
        """

        found = await self._definitions(slug)
        return found[0] if found else None

    async def _definitions(self, slug: str | None) -> list[MasterDetail]:
        query = select(MasterDefinitionModel).where(MasterDefinitionModel.is_active.is_(True))
        if slug is not None:
            query = query.where(MasterDefinitionModel.slug == slug)
        query = query.order_by(MasterDefinitionModel.sort_order, MasterDefinitionModel.name)
        async with self.database.session_factory() as session:
            definitions = list((await session.execute(query)).scalars())
            if not definitions:
                return []
            fields = (
                await session.execute(
                    select(MasterFieldModel)
                    .where(MasterFieldModel.master_id.in_([d.id for d in definitions]))
                    .order_by(MasterFieldModel.sort_order, MasterFieldModel.name)
                )
            ).scalars()
            by_master: dict[UUID, list[MasterField]] = {}
            for item in fields:
                by_master.setdefault(item.master_id, []).append(_field(item))
            return [
                MasterDetail(summary=_summary(model), fields=by_master.get(model.id, []))
                for model in definitions
            ]
