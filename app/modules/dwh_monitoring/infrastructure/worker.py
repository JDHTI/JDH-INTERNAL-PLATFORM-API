"""Background loop that refreshes the copy of the warehouse ``monitoreo`` views."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from app.modules.dwh_monitoring.application.sync import MonitoringSyncService

logger = logging.getLogger(__name__)


class SyncTrigger:
    """Lets ``POST /dwh-monitoring/sync`` cut the worker's wait short."""

    def __init__(self) -> None:
        """Create it unset."""

        self._event = asyncio.Event()

    def set(self) -> None:
        """Ask the worker to load now."""

        self._event.set()

    async def wait(self, timeout: float) -> None:
        """Sleep up to ``timeout`` seconds, or until ``set`` is called."""

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._event.wait(), timeout)
        self._event.clear()


class MonitoringSyncWorker:
    """Run a load cycle at start-up and then every ``interval`` seconds.

    Only this loop loads, so two cycles never overlap: a manual request just wakes
    it. The API runs as a single instance; a second one would load the same rows
    twice, which the upserts make harmless. A failed cycle is logged in
    ``dwh_mon_cargas`` and tried again on the next tick.
    """

    def __init__(
        self,
        service: Callable[[], MonitoringSyncService],
        interval: float,
        trigger: SyncTrigger,
    ) -> None:
        """Create the worker.

        Args:
            service (Callable[[], MonitoringSyncService]): Builds the service for each cycle.
            interval (float): Seconds between cycles.
            trigger (SyncTrigger): Runs a cycle at once when set.
        """

        self._service = service
        self._interval = interval
        self._trigger = trigger
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Start looping in the background."""

        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="dwh-monitoring-sync")

    async def stop(self) -> None:
        """Cancel the loop and wait for it to end."""

        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                cycle = await self._service().run()
                logger.info(
                    "DWH monitoring: cycle %s (%s)", cycle.status, cycle.error or "sin errores"
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("DWH monitoring: cycle failed; it will run again")
            await self._trigger.wait(self._interval)
