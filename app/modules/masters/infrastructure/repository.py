"""PostgreSQL repository for the master catalog."""

from __future__ import annotations

from sqlalchemy import Select, func, or_, select

from app.infrastructure.database import Database
from app.modules.masters.domain.entities import (
    MasterDetail,
    MasterField,
    MasterRecord,
    MasterRecordPage,
    MasterSummary,
)
from app.modules.masters.infrastructure.models import (
    MasterDefinitionModel,
    MasterFieldModel,
    MasterRecordModel,
)


def _summary(model: MasterDefinitionModel, record_count: int) -> MasterSummary:
    return MasterSummary(
        id=model.id,
        slug=model.slug,
        name=model.name,
        description=model.description,
        source_system=model.source_system,
        source_object=model.source_object,
        grain=model.grain,
        natural_key=list(model.natural_key),
        record_count=record_count,
        last_synced_at=model.last_synced_at,
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


def _record(model: MasterRecordModel) -> MasterRecord:
    return MasterRecord(
        id=model.id,
        company_id=model.company_id,
        code=model.code,
        name=model.name,
        is_active=model.is_active,
        source_key=model.source_key,
        attributes=dict(model.attributes),
        synced_at=model.synced_at,
    )


def _count_of(master_id: object) -> Select[tuple[int]]:
    return (
        select(func.count())
        .select_from(MasterRecordModel)
        .where(MasterRecordModel.master_id == master_id)
    )


class SqlAlchemyMasterRepository:
    """Read masters with one short-lived session per operation."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def list_masters(self) -> list[MasterSummary]:
        """List active masters with their record counts."""

        counts = (
            select(MasterRecordModel.master_id, func.count().label("total"))
            .group_by(MasterRecordModel.master_id)
            .subquery()
        )
        query = (
            select(MasterDefinitionModel, func.coalesce(counts.c.total, 0))
            .outerjoin(counts, counts.c.master_id == MasterDefinitionModel.id)
            .where(MasterDefinitionModel.is_active.is_(True))
            .order_by(MasterDefinitionModel.sort_order, MasterDefinitionModel.name)
        )
        async with self.database.session_factory() as session:
            result = await session.execute(query)
            return [_summary(model, int(total)) for model, total in result.all()]

    async def get_master(self, slug: str) -> MasterDetail | None:
        """Return a master with its fields ordered by sort order."""

        async with self.database.session_factory() as session:
            result = await session.execute(
                select(MasterDefinitionModel).where(MasterDefinitionModel.slug == slug)
            )
            model = result.scalar_one_or_none()
            if model is None:
                return None
            total = (await session.execute(_count_of(model.id))).scalar_one()
            fields = await session.execute(
                select(MasterFieldModel)
                .where(MasterFieldModel.master_id == model.id)
                .order_by(MasterFieldModel.sort_order, MasterFieldModel.name)
            )
            return MasterDetail(
                summary=_summary(model, int(total)),
                fields=[_field(row) for row in fields.scalars()],
            )

    async def list_records(
        self,
        slug: str,
        query: str | None,
        is_active: bool | None,
        offset: int,
        limit: int,
    ) -> MasterRecordPage | None:
        """Page through a master's records, filtered by code/name and active flag."""

        async with self.database.session_factory() as session:
            master_id = (
                await session.execute(
                    select(MasterDefinitionModel.id).where(MasterDefinitionModel.slug == slug)
                )
            ).scalar_one_or_none()
            if master_id is None:
                return None
            conditions = [MasterRecordModel.master_id == master_id]
            if query:
                conditions.append(
                    or_(
                        MasterRecordModel.code.icontains(query, autoescape=True),
                        MasterRecordModel.name.icontains(query, autoescape=True),
                    )
                )
            if is_active is not None:
                conditions.append(MasterRecordModel.is_active.is_(is_active))
            total = (
                await session.execute(
                    select(func.count()).select_from(MasterRecordModel).where(*conditions)
                )
            ).scalar_one()
            rows = await session.execute(
                select(MasterRecordModel)
                .where(*conditions)
                .order_by(
                    MasterRecordModel.code.asc().nulls_last(),
                    MasterRecordModel.name.asc().nulls_last(),
                    MasterRecordModel.id,
                )
                .offset(offset)
                .limit(limit)
            )
            return MasterRecordPage(
                items=[_record(row) for row in rows.scalars()],
                total=int(total),
                offset=offset,
                limit=limit,
            )
