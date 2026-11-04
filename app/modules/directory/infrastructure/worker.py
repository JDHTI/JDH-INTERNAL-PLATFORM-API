"""Background loop that syncs the directory every day at a fixed local time."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from functools import partial
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.errors import ConflictError
from app.modules.directory.application.schedule import next_run_at
from app.modules.directory.application.sync import DirectorySyncService
from app.modules.directory.domain.entities import RunTrigger

logger = logging.getLogger(__name__)


class SyncRequest:
    """Hands the worker the runs an administrator asked for (``POST /directory/admin/sync``)."""

    def __init__(self) -> None:
        """Create it empty."""

        self._event = asyncio.Event()
        self._pending: deque[UUID] = deque()

    def set(self, run_id: UUID) -> None:
        """Ask the worker to carry out a run already registered as running.

        Args:
            run_id (UUID): The run.
        """

        self._pending.append(run_id)
        self._event.set()

    async def wait(self, timeout: float) -> UUID | None:
        """Sleep up to ``timeout`` seconds, or until a run is requested.

        Args:
            timeout (float): Seconds to wait.

        Returns:
            UUID | None: The requested run, or ``None`` when the time ran out.
        """

        if not self._pending:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._event.wait(), timeout)
        self._event.clear()
        return self._pending.popleft() if self._pending else None


class DirectorySyncWorker:
    """Sync at the configured local time, and whenever an administrator asks.

    At start-up it closes the runs a stopped process left running and, when
    ``catch_up`` is on and no sync succeeded in the last 24 hours, syncs once.
    Only this loop syncs inside the API; the database lets one run at a time, so the
    manual script and the loop never overlap either.
    """

    def __init__(
        self,
        service: Callable[[], DirectorySyncService],
        trigger: SyncRequest,
        at: time,
        zone: ZoneInfo,
        catch_up: bool = True,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Create the worker.

        Args:
            service (Callable[[], DirectorySyncService]): Builds the service for each sync.
            trigger (SyncRequest): Requested runs.
            at (time): Local time of the daily sync.
            zone (ZoneInfo): Timezone of ``at``.
            catch_up (bool): Sync at start-up when the last success is older than a day.
            clock (Callable[[], datetime] | None): Current instant, for tests.
        """

        self._service = service
        self._trigger = trigger
        self._at = at
        self._zone = zone
        self._catch_up = catch_up
        self._clock = clock or (lambda: datetime.now(UTC))
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Start looping in the background."""

        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="directory-sync")

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
        last_target = self._clock()
        while True:
            # From the last scheduled moment on, so a wake-up a hair early never runs twice.
            target = next_run_at(max(self._clock(), last_target), self._at, self._zone)
            delay = max((target - self._clock()).total_seconds(), 0.0)
            run_id = await self._trigger.wait(delay)
            if run_id is not None:
                await self._guard(partial(self._requested, run_id))
                continue
            last_target = target + timedelta(seconds=1)
            await self._guard(self._scheduled)

    async def _start_up(self) -> None:
        service = self._service()
        recovered = await service.recover_stale_runs()
        if recovered:
            logger.warning("Directory sync: closed %s run(s) left running", recovered)
        if self._catch_up and await service.needs_catch_up():
            await self._scheduled()

    async def _requested(self, run_id: UUID) -> None:
        await self._service().execute(run_id)

    async def _scheduled(self) -> None:
        service = self._service()
        try:
            run = await service.start(RunTrigger.SCHEDULED, None)
        except ConflictError:
            logger.info("Directory sync: another sync is running; skipping this one")
            return
        await service.execute(run.id)

    async def _guard(self, action) -> None:
        try:
            await action()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Directory sync failed; it will run again at the next schedule")
