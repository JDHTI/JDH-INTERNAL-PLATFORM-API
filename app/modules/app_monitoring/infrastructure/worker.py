"""Background loop that checks every enabled application."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from app.modules.app_monitoring.application.service import AppMonitoringService
from app.modules.app_monitoring.domain.entities import CheckStatus
from app.modules.dwh_monitoring.infrastructure.worker import SyncTrigger

logger = logging.getLogger(__name__)

__all__ = ["AppCheckWorker", "SyncTrigger"]


class AppCheckWorker:
    """Check at start-up and then every ``interval`` seconds.

    Only this loop runs the scheduled checks; ``POST /app-monitoring/check`` just
    wakes it. A failed cycle (the database down, say) is logged and tried again.
    """

    def __init__(
        self,
        service: Callable[[], AppMonitoringService],
        interval: float,
        trigger: SyncTrigger,
    ) -> None:
        """Create the worker.

        Args:
            service (Callable[[], AppMonitoringService]): Builds the service for each cycle.
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
            self._task = asyncio.create_task(self._run(), name="app-monitoring-checks")

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
                results = await self._service().check_all()
                down = [r for r in results if r.status is CheckStatus.DOWN]
                logger.info("App monitoring: %d checks, %d down", len(results), len(down))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("App monitoring: cycle failed; it will run again")
            await self._trigger.wait(self._interval)
