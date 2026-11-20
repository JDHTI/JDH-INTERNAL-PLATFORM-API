"""Run the background jobs when the warehouse data they read is fresh.

Rules:
- Every ``poll_seconds`` the worker reads ``monitoreo.secuencias`` live. A sequence that
  finished well (``success``, not reloading) with a newer ``ultima_datos_listos`` than
  the last one dispatched queues its jobs (``trigger=dwh``). The very first poll, with
  nothing stored, dispatches once; later restarts dispatch nothing new.
- Every day at ``fallback_at`` a job whose sequence did not trigger it that day runs
  anyway (``trigger=scheduled``), on the weekdays the sequence runs.
- A job with a fixed time (``at``, no sequence) runs every day at that local time
  (``trigger=scheduled``); a start-up after it runs it if it has not run since.
- At start-up, runs a stopped process left open are closed as failed, and a job whose
  sequence has fresher data than its last success runs (``trigger=startup``).
- One open run per job (the database enforces it). Runs execute one at a time.
- Before running, a job waits while its sequence is reloading (dbt drops the views).
- A run whose detail lists errors is ``partial``; an exception makes it ``failed``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.infrastructure.data_provider import DataProviderError
from app.modules.jobs.domain.entities import JobOverview, JobRun, SequenceState
from app.modules.jobs.domain.ports import (
    JobHandler,
    JobRunRepository,
    SequenceSource,
    SequenceWatermarks,
)
from app.modules.jobs.domain.registry import (
    JOBS,
    JOBS_BY_KEY,
    RunStatus,
    Trigger,
    jobs_for,
    sequences,
    timed_jobs,
)

logger = logging.getLogger(__name__)

MAX_HISTORY = 100
_ERROR_LENGTH = 1000


@dataclass
class PollStatus:
    """The last poll of the warehouse, shared between the worker and the API."""

    last_poll_at: datetime | None = None
    error: str | None = None


@dataclass(frozen=True)
class JobsSettings:
    """How the jobs run, as ``GET /jobs`` shows it."""

    enabled: bool
    poll_seconds: int
    fallback_at: time
    timezone: str


def _error(exc: BaseException) -> str:
    message = str(exc) or type(exc).__name__
    cause = exc.__cause__
    if cause is not None and str(cause) and str(cause) not in message:
        message = f"{message}: {cause}"
    return message[:_ERROR_LENGTH]


class JobService:
    """Decide when each job runs, run it and keep its history."""

    def __init__(
        self,
        runs: JobRunRepository,
        watermarks: SequenceWatermarks,
        sequences: SequenceSource,
        handlers: Mapping[str, JobHandler],
        status: PollStatus,
        settings: JobsSettings,
        request_run: Callable[[UUID], None],
        wait_for_dwh_seconds: float = 1800,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Create the service.

        Args:
            runs (JobRunRepository): Run history.
            watermarks (SequenceWatermarks): Sequences already seen and dispatched.
            sequences (SequenceSource): Live warehouse sequence states.
            handlers (Mapping[str, JobHandler]): The work of each job key.
            status (PollStatus): Last poll result, shared with the API.
            settings (JobsSettings): Switch, poll interval and fallback time.
            request_run (Callable[[UUID], None]): Hands a queued run to the worker.
            wait_for_dwh_seconds (float): Longest wait for a reloading sequence.
            clock (Callable[[], datetime] | None): Current instant, for tests.
            sleep (Callable[[float], Awaitable[None]]): Waits, for tests.
        """

        self.runs = runs
        self.watermarks = watermarks
        self.sequences = sequences
        self.handlers = handlers
        self.status = status
        self.settings = settings
        self.request_run_now = request_run
        self.wait_for_dwh_seconds = wait_for_dwh_seconds
        self.clock = clock or (lambda: datetime.now(UTC))
        self.sleep = sleep

    # --- Queueing ---

    async def _queue(
        self, key: str, trigger: Trigger, state: SequenceState | None = None
    ) -> JobRun | None:
        try:
            run = await self.runs.queue(
                key,
                trigger,
                self.clock(),
                corrida_id=state.ultima_corrida_id if state else None,
                datos_listos=state.ultima_datos_listos if state else None,
            )
        except ConflictError:
            logger.info("Jobs: %s already has an open run; not queued again", key)
            return None
        return run

    async def request_run(self, key: str, triggered_by: str | None) -> JobRun:
        """Queue a run by hand (``POST /jobs/{key}/run``).

        Args:
            key (str): Job key.
            triggered_by (str | None): Who asked.

        Returns:
            JobRun: The queued run.

        Raises:
            NotFoundError: For an unknown job.
            ConflictError: When jobs are off or the job already has an open run.
        """

        if key not in JOBS_BY_KEY or key not in self.handlers:
            raise NotFoundError("Tarea no encontrada")
        if not self.settings.enabled:
            raise ConflictError("Las tareas en segundo plano están apagadas (JOBS_ENABLED)")
        try:
            run = await self.runs.queue(
                key, Trigger.MANUAL, self.clock(), triggered_by=triggered_by
            )
        except ConflictError as exc:
            raise ConflictError("Esa tarea ya está en cola o en curso") from exc
        self.request_run_now(run.id)
        return run

    async def poll_dwh(self) -> list[UUID]:
        """Read the sequences and queue the jobs of those with new fresh data.

        Returns:
            list[UUID]: The runs queued.
        """

        now = self.clock()
        try:
            live = await self.sequences.states(sequences())
        except DataProviderError as exc:
            if self.status.error is None:
                logger.warning("Jobs: the warehouse could not be polled: %s", _error(exc))
            self.status.error = _error(exc)
            return []
        if self.status.error is not None:
            logger.info("Jobs: the warehouse answers again")
        self.status.error = None
        self.status.last_poll_at = now
        stored = await self.watermarks.all()
        queued: list[UUID] = []
        saved: list[SequenceState] = []
        # Registry order: identity before products before supply (costs need the products).
        order = {name: index for index, name in enumerate(sequences())}
        for state in sorted(live, key=lambda s: order.get(s.secuencia, len(order))):
            previous = stored.get(state.secuencia)
            state = replace(
                state,
                dispatched_datos_listos=previous.dispatched_datos_listos if previous else None,
                polled_at=now,
            )
            if state.fresh and state.advanced:
                for job in jobs_for(state.secuencia):
                    run = await self._queue(job.key, Trigger.DWH, state)
                    if run is not None:
                        queued.append(run.id)
                logger.info(
                    "Jobs: %s has fresh data (%s); jobs queued",
                    state.secuencia,
                    state.ultima_datos_listos,
                )
                state = replace(state, dispatched_datos_listos=state.ultima_datos_listos)
            saved.append(state)
        await self.watermarks.save(saved)
        return queued

    async def catch_up(self) -> list[UUID]:
        """Queue the jobs whose sequence has fresher data than their last success."""

        stored = await self.watermarks.all()
        queued: list[UUID] = []
        for job in JOBS:
            state = stored.get(job.sequence) if job.sequence else None
            if state is None or not state.fresh:
                continue
            last = await self.runs.last_success(job.key)
            if last is None or (last.started_at or last.queued_at) < state.ultima_datos_listos:
                run = await self._queue(job.key, Trigger.STARTUP, state)
                if run is not None:
                    queued.append(run.id)
        return queued

    async def fallback(self) -> list[UUID]:
        """Queue the jobs due today that have not run since local midnight."""

        zone = ZoneInfo(self.settings.timezone)
        local = self.clock().astimezone(zone)
        midnight = datetime.combine(local.date(), time(0), tzinfo=zone)
        queued: list[UUID] = []
        for job in JOBS:
            if job.sequence is None or local.isoweekday() not in job.fallback_days:
                continue
            last = await self.runs.last_success(job.key)
            if last is not None and (last.started_at or last.queued_at) >= midnight:
                continue
            run = await self._queue(job.key, Trigger.SCHEDULED)
            if run is not None:
                logger.warning(
                    "Jobs: %s did not run from the warehouse today; running now", job.key
                )
                queued.append(run.id)
        return queued

    async def timed(self) -> list[UUID]:
        """Queue the jobs with a fixed time (``at``) that is already past today and that
        have not run since then: on time from the worker, or late after a start-up."""

        zone = ZoneInfo(self.settings.timezone)
        local = self.clock().astimezone(zone)
        queued: list[UUID] = []
        for job in timed_jobs():
            if job.at is None or local.isoweekday() not in job.fallback_days:
                continue
            due = datetime.combine(local.date(), job.at, tzinfo=zone)
            if local < due:
                continue
            last = await self.runs.last_success(job.key)
            if last is not None and (last.started_at or last.queued_at) >= due:
                continue
            run = await self._queue(job.key, Trigger.SCHEDULED)
            if run is not None:
                queued.append(run.id)
        return queued

    async def recover_stale_runs(self) -> int:
        """Close as failed the runs a stopped process left open."""

        return await self.runs.fail_open("El proceso se detuvo antes de terminar", self.clock())

    # --- Running ---

    async def _wait_for_sequence(self, sequence: str) -> None:
        waited = 0.0
        while True:
            try:
                states = await self.sequences.states([sequence])
            except DataProviderError:
                return  # The job itself reports the warehouse error.
            if not states or not states[0].en_curso:
                return
            if waited >= self.wait_for_dwh_seconds:
                raise ConflictError(f"La secuencia {sequence} del DWH sigue en curso")
            step = min(60.0, self.wait_for_dwh_seconds - waited) or 60.0
            await self.sleep(step)
            waited += step

    async def execute(self, run_id: UUID) -> JobRun:
        """Run a queued job and record how it went.

        Args:
            run_id (UUID): The queued run.

        Returns:
            JobRun: The closed run.
        """

        run = await self.runs.mark_running(run_id, self.clock())
        definition = JOBS_BY_KEY.get(run.job)
        handler = self.handlers.get(run.job)
        try:
            if definition is None or handler is None:
                raise NotFoundError(f"La tarea {run.job} ya no existe")
            if definition.sequence:
                await self._wait_for_sequence(definition.sequence)
            detail = dict(await handler())
        except asyncio.CancelledError:
            await self.runs.finish(
                run_id, RunStatus.FAILED, {}, self.clock(), "Cancelada al detener el proceso"
            )
            raise
        except Exception as exc:
            logger.exception("Jobs: %s failed", run.job)
            return await self.runs.finish(run_id, RunStatus.FAILED, {}, self.clock(), _error(exc))
        status = RunStatus.PARTIAL if detail.get("errors") else RunStatus.SUCCESS
        finished = await self.runs.finish(run_id, status, detail, self.clock())
        logger.info("Jobs: %s finished %s", run.job, status.value)
        return finished

    # --- Reading ---

    async def overview(self) -> tuple[JobsSettings, PollStatus, list[JobOverview]]:
        """Every job with its last run, last success and the state of its sequence."""

        stored = await self.watermarks.all()
        items = []
        for job in JOBS:
            last = await self.runs.latest(job.key)
            success = await self.runs.last_success(job.key)
            items.append(
                JobOverview(
                    key=job.key,
                    label=job.label,
                    description=job.description,
                    sequence=job.sequence,
                    fallback_days=sorted(job.fallback_days),
                    last_run=last,
                    last_success_at=(success.finished_at if success else None),
                    sequence_state=stored.get(job.sequence) if job.sequence else None,
                    at=job.at.strftime("%H:%M") if job.at else None,
                )
            )
        return self.settings, self.status, items

    async def history(self, job: str | None, limit: int) -> list[JobRun]:
        """Newest runs first.

        Raises:
            NotFoundError: For an unknown job.
            InvalidInputError: When the limit is out of range.
        """

        if job is not None and job not in JOBS_BY_KEY:
            raise NotFoundError("Tarea no encontrada")
        if not 1 <= limit <= MAX_HISTORY:
            raise InvalidInputError(f"El límite debe estar entre 1 y {MAX_HISTORY}")
        return await self.runs.history(job, limit)
