"""PostgreSQL persistence of the job runs and the sequence watermarks."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError
from app.infrastructure.database import Database
from app.modules.jobs.domain.entities import JobRun, SequenceState
from app.modules.jobs.domain.registry import DONE_STATUSES, OPEN_STATUSES, RunStatus, Trigger
from app.modules.jobs.infrastructure.models import DwhSequenceWatermarkModel, JobRunModel

_OPEN = [status.value for status in OPEN_STATUSES]
_DONE = [status.value for status in DONE_STATUSES]
_STATE_FIELDS = (
    "ultima_corrida_id",
    "ultima_estado",
    "ultima_datos_listos",
    "en_curso",
    "proxima_ejecucion",
    "eta_datos_listos_proxima",
    "eta_datos_listos_en_curso",
    "dispatched_datos_listos",
    "polled_at",
)


def _run(model: JobRunModel) -> JobRun:
    return JobRun(
        id=model.id,
        job=model.job,
        trigger=Trigger(model.trigger),
        status=RunStatus(model.status),
        queued_at=model.queued_at,
        started_at=model.started_at,
        finished_at=model.finished_at,
        detail=dict(model.detail or {}),
        error=model.error,
        dwh_corrida_id=model.dwh_corrida_id,
        dwh_datos_listos=model.dwh_datos_listos,
        triggered_by=model.triggered_by,
    )


class SqlAlchemyJobRunRepository:
    """History of the job runs; one open run per job (``uq_job_runs_open``)."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def queue(
        self,
        job: str,
        trigger: Trigger,
        now: datetime,
        *,
        triggered_by: str | None = None,
        corrida_id: UUID | None = None,
        datos_listos: datetime | None = None,
    ) -> JobRun:
        """Register a queued run; ``ConflictError`` when the job has an open one."""

        model = JobRunModel(
            id=uuid.uuid4(),
            job=job,
            trigger=trigger.value,
            status=RunStatus.QUEUED.value,
            queued_at=now,
            detail={},
            dwh_corrida_id=corrida_id,
            dwh_datos_listos=datos_listos,
            triggered_by=triggered_by,
        )
        async with self.database.session_factory() as session:
            session.add(model)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError(f"La tarea {job} ya tiene una ejecución abierta") from exc
            return _run(model)

    async def _change(self, run_id: UUID, **values: Any) -> JobRun:
        async with self.database.session_factory() as session:
            model = await session.get(JobRunModel, run_id)
            if model is None:
                raise ValueError(f"Unknown job run {run_id}")
            for name, value in values.items():
                setattr(model, name, value)
            await session.commit()
            return _run(model)

    async def mark_running(self, run_id: UUID, now: datetime) -> JobRun:
        """Mark a queued run as running."""

        return await self._change(run_id, status=RunStatus.RUNNING.value, started_at=now)

    async def finish(
        self,
        run_id: UUID,
        status: RunStatus,
        detail: Mapping[str, Any],
        now: datetime,
        error: str | None = None,
    ) -> JobRun:
        """Close a run."""

        return await self._change(
            run_id, status=status.value, detail=dict(detail), finished_at=now, error=error
        )

    async def get(self, run_id: UUID) -> JobRun | None:
        """One run."""

        async with self.database.session_factory() as session:
            model = await session.get(JobRunModel, run_id)
            return _run(model) if model else None

    async def _newest(self, *conditions) -> JobRun | None:
        async with self.database.session_factory() as session:
            model = await session.scalar(
                select(JobRunModel)
                .where(*conditions)
                .order_by(JobRunModel.queued_at.desc())
                .limit(1)
            )
            return _run(model) if model else None

    async def latest(self, job: str) -> JobRun | None:
        """The newest run of a job."""

        return await self._newest(JobRunModel.job == job)

    async def last_success(self, job: str) -> JobRun | None:
        """The newest run of a job that finished (success or partial)."""

        return await self._newest(JobRunModel.job == job, JobRunModel.status.in_(_DONE))

    async def history(self, job: str | None, limit: int) -> list[JobRun]:
        """Newest runs first, of one job or of all."""

        query = select(JobRunModel).order_by(JobRunModel.queued_at.desc()).limit(limit)
        if job is not None:
            query = query.where(JobRunModel.job == job)
        async with self.database.session_factory() as session:
            return [_run(model) for model in await session.scalars(query)]

    async def fail_open(self, error: str, now: datetime) -> int:
        """Close every open run as failed; return how many."""

        async with self.database.session_factory() as session:
            result = await session.execute(
                update(JobRunModel)
                .where(JobRunModel.status.in_(_OPEN))
                .values(status=RunStatus.FAILED.value, finished_at=now, error=error)
            )
            await session.commit()
            return int(result.rowcount or 0)


class SqlAlchemySequenceWatermarks:
    """The warehouse sequences as last polled, with what was dispatched for each."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def all(self) -> dict[str, SequenceState]:
        """Every stored sequence, by name."""

        async with self.database.session_factory() as session:
            rows = await session.scalars(select(DwhSequenceWatermarkModel))
            return {
                row.secuencia: SequenceState(
                    secuencia=row.secuencia,
                    **{name: getattr(row, name) for name in _STATE_FIELDS},
                )
                for row in rows
            }

    async def save(self, states: Sequence[SequenceState]) -> None:
        """Insert or update by sequence name."""

        if not states:
            return
        rows = [
            {"secuencia": s.secuencia, **{name: getattr(s, name) for name in _STATE_FIELDS}}
            for s in states
        ]
        statement = insert(DwhSequenceWatermarkModel).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=["secuencia"],
            set_={name: getattr(statement.excluded, name) for name in _STATE_FIELDS},
        )
        async with self.database.session_factory() as session:
            await session.execute(statement)
            await session.commit()
