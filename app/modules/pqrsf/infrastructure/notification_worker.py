"""Background loop that runs the PQRSF timers and delivers the queued emails."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from app.modules.pqrsf.application.notifications import NotificationService

logger = logging.getLogger(__name__)


class NotificationWorker:
    """Every ``interval`` seconds: start the timers that are due, then send what is queued.

    The API runs as a single instance. Even so, a timer is claimed once per day in the
    database and every email carries a dedupe key, so a restart or a retry never repeats
    a report. A failed pass is logged and tried again on the next tick.
    """

    def __init__(self, service: Callable[[], NotificationService], interval: float = 30.0) -> None:
        """Create the worker.

        Args:
            service (Callable[[], NotificationService]): Builds the service for each pass.
            interval (float): Seconds between passes.
        """

        self._service = service
        self._interval = interval
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Start looping in the background."""

        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="pqrsf-notifications")

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
            service = self._service()
            for step in (service.run_scheduled, service.dispatch):
                try:
                    await step()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "PQRSF notifications: %s failed; it will run again", step.__name__
                    )
            await asyncio.sleep(self._interval)
