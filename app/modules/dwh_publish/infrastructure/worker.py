"""Background loop that empties the outbox and reconciles once a day."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from app.modules.dwh_publish.application.service import DwhPublishService
from app.modules.dwh_publish.domain.ports import DwhUnavailable
from app.modules.jobs.application.schedule import next_run_at

logger = logging.getLogger(__name__)


class DwhPublishWorker:
    """Every ``poll_seconds`` send a batch (drain while full batches come); pause while the
    breaker is open; reconcile daily at ``reconcile_at`` (local time, the DWH's quiet hours).

    One process runs it; the leases in the outbox keep two loops from sending the same event.
    """

    def __init__(
        self,
        service: Callable[[], DwhPublishService],
        poll_seconds: float,
        reconcile_at: time,
        zone: ZoneInfo,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Create the worker.

        Args:
            service (Callable[[], DwhPublishService]): Builds the service for each pass.
            poll_seconds (float): Seconds between passes when the queue is empty.
            reconcile_at (time): Local time of the daily reconciliation.
            zone (ZoneInfo): Timezone of ``reconcile_at``.
            clock (Callable[[], datetime] | None): Current instant, for tests.
        """

        self._service = service
        self._poll_seconds = poll_seconds
        self._at = reconcile_at
        self._zone = zone
        self._clock = clock or (lambda: datetime.now(UTC))
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Start looping in the background."""

        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="dwh-publish")

    async def stop(self) -> None:
        """Cancel the loop and wait for it to end."""

        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        next_reconcile = next_run_at(self._clock(), self._at, self._zone)
        while True:
            service = self._service()
            drained = False
            if not service.paused():
                drained = await self._guard(service)
            if self._clock() >= next_reconcile:
                await self._reconcile(service)
                next_reconcile = next_run_at(self._clock(), self._at, self._zone)
            if not drained:
                await asyncio.sleep(self._poll_seconds)

    async def _guard(self, service: DwhPublishService) -> bool:
        """One pass; True when a full batch went out and more may be waiting."""

        try:
            result = await service.publish_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("DWH publish: a pass failed; the loop goes on")
            return False
        if result.unavailable:
            logger.warning("DWH publish: warehouse unavailable (%s)", service.state.last_error)
            return False
        if result.failed:
            logger.error("DWH publish: %s event(s) set aside as failed", result.failed)
        return result.claimed >= service.settings.batch_size

    async def _reconcile(self, service: DwhPublishService) -> None:
        if not service.configured:
            return
        try:
            for item in await service.reconcile():
                if item.queued:
                    logger.warning(
                        "DWH publish: reconciliation queued %s %s record(s) again",
                        item.queued,
                        item.topic,
                    )
        except asyncio.CancelledError:
            raise
        except DwhUnavailable as exc:
            logger.warning("DWH publish: reconciliation skipped, warehouse unavailable: %s", exc)
        except Exception:
            logger.exception("DWH publish: reconciliation failed")


__all__ = ["DwhPublishWorker"]
