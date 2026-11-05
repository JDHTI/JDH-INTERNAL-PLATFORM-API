"""Background loop of the jobs: polls the warehouse, runs the daily fallback and the runs."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from functools import partial
from uuid import UUID
from zoneinfo import ZoneInfo

from app.modules.jobs.application.schedule import next_run_at
from app.modules.jobs.application.service import JobService

logger = logging.getLogger(__name__)


class RunRequest:
    """Hands the worker the runs that were queued (by the DWH poll, the fallback or a person)."""

    def __init__(self) -> None:
        """Create it empty."""

        self._event = asyncio.Event()
        self._pending: deque[UUID] = deque()

    def set(self, run_id: UUID) -> None:
        """Ask the worker to execute a queued run.

        Args:
            run_id (UUID): The run.
        """

        self._pending.append(run_id)
        self._event.set()

    async def wait(self, timeout: float) -> UUID | None:
        """Sleep up to ``timeout`` seconds, or until a run is requested.

        Returns:
            UUID | None: The next run, or ``None`` when the time ran out.
        """

        if not self._pending:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._event.wait(), timeout)
        self._event.clear()
        return self._pending.popleft() if self._pending else None


class JobsWorker:
    """Poll the warehouse every ``poll_seconds``, fall back daily, run one job at a time.

    The API runs as one instance; the database still allows one open run per job, so a
    manual request and the loop never run the same job twice at once.
    """

    def __init__(
        self,
        service: Callable[[], JobService],
        trigger: RunRequest,
        poll_seconds: float,
        fallback_at: time,
        zone: ZoneInfo,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Create the worker.

        Args:
            service (Callable[[], JobService]): Builds the service for each step.
            trigger (RunRequest): Queued runs to execute.
            poll_seconds (float): Seconds between warehouse polls.
            fallback_at (time): Local time of the daily fallback.
            zone (ZoneInfo): Timezone of ``fallback_at``.
            clock (Callable[[], datetime] | None): Current instant, for tests.
        """

        self._service = service
        self._trigger = trigger
        self._poll_seconds = poll_seconds
        self._at = fallback_at
        self._zone = zone
        self._clock = clock or (lambda: datetime.now(UTC))
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Start looping in the background."""

        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="jobs")

    async def stop(self) -> None:
        """Cancel the loop and wait for it to end."""

        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        await self._guard(self._start_up)
        next_poll = self._clock() + timedelta(seconds=self._poll_seconds)
        next_fallback = next_run_at(self._clock(), self._at, self._zone)
        while True:
            target = min(next_poll, next_fallback)
            delay = max((target - self._clock()).total_seconds(), 0.0)
            run_id = await self._trigger.wait(delay)
            if run_id is not None:
                await self._guard(partial(self._execute, run_id))
                continue
            # The wait ended at the target: a wake-up a hair early still counts as due.
            now = max(self._clock(), target)
            if now >= next_fallback:
                await self._guard(partial(self._dispatch, lambda s: s.fallback()))
                next_fallback = next_run_at(now + timedelta(seconds=1), self._at, self._zone)
            if now >= next_poll:
                await self._guard(partial(self._dispatch, lambda s: s.poll_dwh()))
                next_poll = now + timedelta(seconds=self._poll_seconds)

    async def _start_up(self) -> None:
        service = self._service()
        recovered = await service.recover_stale_runs()
        if recovered:
            logger.warning("Jobs: closed %s run(s) left open", recovered)
        await self._dispatch(lambda s: s.poll_dwh())
        await self._dispatch(lambda s: s.catch_up())

    async def _dispatch(self, step: Callable[[JobService], Awaitable[list[UUID]]]) -> None:
        for run_id in await step(self._service()):
            self._trigger.set(run_id)

    async def _execute(self, run_id: UUID) -> None:
        await self._service().execute(run_id)

    async def _guard(self, action) -> None:
        try:
            await action()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Jobs: a step failed; the loop goes on")
