"""Background loop that sends the scheduled notifications when their time comes."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from app.modules.notifications.application.service import NotificationService

logger = logging.getLogger(__name__)


class NotificationDispatchWorker:
    """Every ``interval`` seconds, send what is due (scheduled posts and reminders).

    The API runs as one instance; ``claim`` in the database still makes a second
    sender harmless.
    """

    def __init__(self, service: Callable[[], NotificationService], interval: float) -> None:
        """Create the worker.

        Args:
            service (Callable[[], NotificationService]): Builds the service each cycle.
            interval (float): Seconds between cycles.
        """

        self._service = service
        self._interval = interval
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Start looping in the background."""

        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="notifications-dispatch")

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
                sent, cancelled = await self._service().dispatch_due()
                if sent or cancelled:
                    logger.info("Notifications: %s sent, %s cancelled (late)", sent, cancelled)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Notifications dispatch failed; it will run again")
            await asyncio.sleep(self._interval)
