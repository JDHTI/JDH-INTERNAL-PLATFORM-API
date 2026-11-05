"""Contracts of the jobs module."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.modules.jobs.domain.entities import JobRun, SequenceState
from app.modules.jobs.domain.registry import RunStatus, Trigger

# A job's work: returns the detail stored with the run. ``{"errors": [...]}`` makes the
# run partial.
JobHandler = Callable[[], Awaitable[Mapping[str, Any]]]


class JobRunRepository(Protocol):
    """History of the runs; at most one open (queued or running) run per job."""

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
        """Register a queued run.

        Raises:
            ConflictError: When the job already has an open run.
        """
        ...

    async def mark_running(self, run_id: UUID, now: datetime) -> JobRun:
        """Mark a queued run as running."""
        ...

    async def finish(
        self,
        run_id: UUID,
        status: RunStatus,
        detail: Mapping[str, Any],
        now: datetime,
        error: str | None = None,
    ) -> JobRun:
        """Close a run."""
        ...

    async def get(self, run_id: UUID) -> JobRun | None:
        """One run."""
        ...

    async def latest(self, job: str) -> JobRun | None:
        """The newest run of a job."""
        ...

    async def last_success(self, job: str) -> JobRun | None:
        """The newest run of a job that finished (success or partial)."""
        ...

    async def history(self, job: str | None, limit: int) -> list[JobRun]:
        """Newest runs first, of one job or of all."""
        ...

    async def fail_open(self, error: str, now: datetime) -> int:
        """Close every open run as failed (a stopped process left them); return how many."""
        ...


class SequenceWatermarks(Protocol):
    """The last state of each DWH sequence the platform saw and dispatched."""

    async def all(self) -> dict[str, SequenceState]:
        """Every stored sequence, by name."""
        ...

    async def save(self, states: Sequence[SequenceState]) -> None:
        """Insert or update by sequence name."""
        ...


class SequenceSource(Protocol):
    """Live read of ``monitoreo.secuencias`` in the warehouse."""

    async def states(self, sequences: Sequence[str]) -> list[SequenceState]:
        """The current state of these sequences.

        Raises:
            DataProviderUnavailable: When the warehouse cannot be read.
        """
        ...
