"""PostgreSQL repository for the master catalog: reads, and the writes of the sync."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.database import Database
from app.modules.masters.domain.entities import (
    MasterDetail,
    MasterField,
    MasterRecord,
    MasterRecordPage,
    MasterSummary,
)
from app.modules.masters.domain.ports import Row
from app.modules.masters.infrastructure.models import (
    MasterDefinitionModel,
    MasterFieldModel,
    MasterRecordModel,
)

# Rows per INSERT: 8 columns each stays far below PostgreSQL's 32767 parameters.
_UPSERT_CHUNK = 1000


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

    # --- Writes of the warehouse sync ---

    async def list_definitions(self) -> list[MasterDetail]:
        """Active masters with their fields, in sort order (counts are not loaded)."""

        async with self.database.session_factory() as session:
            definitions = (
                await session.execute(
                    select(MasterDefinitionModel)
                    .where(MasterDefinitionModel.is_active.is_(True))
                    .order_by(MasterDefinitionModel.sort_order, MasterDefinitionModel.name)
                )
            ).scalars()
            definitions = list(definitions)
            fields = (
                await session.execute(
                    select(MasterFieldModel)
                    .where(MasterFieldModel.master_id.in_([d.id for d in definitions]))
                    .order_by(MasterFieldModel.sort_order, MasterFieldModel.name)
                )
            ).scalars()
            by_master: dict[UUID, list] = {}
            for field in fields:
                by_master.setdefault(field.master_id, []).append(_field(field))
            return [
                MasterDetail(summary=_summary(model, 0), fields=by_master.get(model.id, []))
                for model in definitions
            ]

    async def upsert_records(
        self, master_id: UUID, rows: Sequence[Row], synced_at: datetime
    ) -> int:
        """Insert or update records by ``(master_id, source_key)``.

        Args:
            master_id (UUID): The master.
            rows (Sequence[Row]): Mapped records (see ``mapping.record_row``).
            synced_at (datetime): Start of the sync; marks the rows it saw.

        Returns:
            int: Rows written.
        """

        # One row per key: a repeated key inside one INSERT ... ON CONFLICT fails.
        rows = list({row["source_key"]: row for row in rows}.values())
        written = 0
        async with self.database.session_factory() as session:
            for start in range(0, len(rows), _UPSERT_CHUNK):
                chunk = rows[start : start + _UPSERT_CHUNK]
                statement = insert(MasterRecordModel).values(
                    [
                        {
                            "id": uuid.uuid4(),
                            "master_id": master_id,
                            "company_id": row["company_id"],
                            "code": row["code"],
                            "name": row["name"],
                            "is_active": row["is_active"],
                            "source_key": row["source_key"],
                            "attributes": row["attributes"],
                            "synced_at": synced_at,
                        }
                        for row in chunk
                    ]
                )
                excluded = statement.excluded
                await session.execute(
                    statement.on_conflict_do_update(
                        constraint="uq_master_records_master_source_key",
                        set_={
                            "company_id": excluded.company_id,
                            "code": excluded.code,
                            "name": excluded.name,
                            "is_active": excluded.is_active,
                            "attributes": excluded.attributes,
                            "synced_at": excluded.synced_at,
                            "updated_at": func.now(),
                        },
                    )
                )
                written += len(chunk)
            await session.commit()
        return written

    async def deactivate_unseen(self, master_id: UUID, synced_at: datetime) -> int:
        """Mark inactive the records of the master this sync did not see.

        Args:
            master_id (UUID): The master.
            synced_at (datetime): Start of the sync.

        Returns:
            int: Records deactivated.
        """

        async with self.database.session_factory() as session:
            result = await session.execute(
                update(MasterRecordModel)
                .where(
                    MasterRecordModel.master_id == master_id,
                    MasterRecordModel.is_active.is_not(False),
                    or_(
                        MasterRecordModel.synced_at.is_(None),
                        MasterRecordModel.synced_at < synced_at,
                    ),
                )
                .values(is_active=False, updated_at=func.now())
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def touch_definition(self, master_id: UUID, synced_at: datetime) -> None:
        """Record when the master was last synced."""

        async with self.database.session_factory() as session:
            await session.execute(
                update(MasterDefinitionModel)
                .where(MasterDefinitionModel.id == master_id)
                .values(last_synced_at=synced_at)
            )
            await session.commit()
