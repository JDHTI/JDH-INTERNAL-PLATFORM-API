"""Background loop that runs the PQRSF timers and delivers the queued emails."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from app.modules.pqrsf.application.notifications import NotificationService

logger = logging.getLogger(__name__)


class Wakeup:
    """Lets the code that queues an email cut the worker's wait short.

    Used where a reply should leave right away (the thank-you to a suggestion) instead of
    waiting for the next pass.
    """

    def __init__(self) -> None:
        """Create it unset."""

        self._event = asyncio.Event()

    def set(self) -> None:
        """Ask the worker to run now."""

        self._event.set()

    async def wait(self, timeout: float) -> None:
        """Sleep up to ``timeout`` seconds, or until ``set`` is called."""

        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except TimeoutError:
            pass
        self._event.clear()


class NotificationWorker:
    """Every ``interval`` seconds: start the timers that are due, then send what is queued.

    The API runs as a single instance. Even so, a timer is claimed once per day in the
    database and every email carries a dedupe key, so a restart or a retry never repeats
    a report. A failed pass is logged and tried again on the next tick.
    """

    def __init__(
        self,
        service: Callable[[], NotificationService],
        interval: float = 30.0,
        wakeup: Wakeup | None = None,
    ) -> None:
        """Create the worker.

        Args:
            service (Callable[[], NotificationService]): Builds the service for each pass.
            interval (float): Seconds between passes.
            wakeup (Wakeup | None): Runs a pass at once when set by whoever queued an email.
        """

        self._service = service
        self._interval = interval
        self._wakeup = wakeup
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
            if self._wakeup is None:
                await asyncio.sleep(self._interval)
            else:
                await self._wakeup.wait(self._interval)
