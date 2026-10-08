"""Background loop that keeps the PQRSF mailbox in sync with the cases."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from app.modules.pqrsf.application.email_intake import EmailIntakeService

logger = logging.getLogger(__name__)


class EmailIntakeWorker:
    """Run one sync every ``interval`` seconds until stopped.

    The API runs as a single instance, so one loop is enough; a failed pass is
    logged and tried again on the next tick.
    """

    def __init__(self, service: Callable[[], EmailIntakeService], interval: float) -> None:
        """Create the worker.

        Args:
            service (Callable[[], EmailIntakeService]): Builds the service for each pass.
            interval (float): Seconds between passes.
        """

        self._service = service
        self._interval = interval
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Start looping in the background."""

        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="pqrsf-email-intake")

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
                await self._service().sync()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("PQRSF mail sync failed; it will run again")
            await asyncio.sleep(self._interval)
