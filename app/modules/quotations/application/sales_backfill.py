"""The last sales of a list update, read again in the background (2026-11-20).

A list update is built even when the warehouse does not answer with the last sales in time:
it carries the warning ``ultima_venta_no_leida``. Then this reads them again a few times,
waiting longer each time, and when it can it writes them on the lines and drops the
warning, without a new version (the editor keeps saving prices with the version it has).

In a list update the last sale only informs (see :func:`rules.list_sale_fields`): no price,
margin or count changes when it arrives.

The tries live in the API process: a restart loses them and the warning stays until a
refresh, as before. One retry per list update at a time.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from app.core.errors import DependencyUnavailableError
from app.modules.quotations.domain import rules, vocabulary
from app.modules.quotations.domain.ports import QuotationRepository, WarehouseSource

logger = logging.getLogger(__name__)

# Seconds before each try: about 8 minutes of waits, plus the reads.
DELAYS: tuple[float, ...] = (15.0, 30.0, 60.0, 120.0, 240.0)
# Seconds each read may take: more than a request's 90 s (the main lists' sync gives 180).
READ_TIMEOUT = 150.0
# A save in flight while the sales were written may put the warning back (it read the header
# before): checked again after these seconds, and written again if so.
RECHECKS: tuple[float, ...] = (5.0, 30.0)

# Strong references to the running tries (asyncio keeps only weak ones), by list update.
_RUNNING: dict[UUID, asyncio.Task[bool]] = {}


def longest_seconds(
    delays: Sequence[float] = DELAYS,
    timeout: float = READ_TIMEOUT,
    rechecks: Sequence[float] = RECHECKS,
) -> float:
    """The longest a retry can take: every wait, every read to its limit and the rechecks."""

    return sum(delays) + timeout * len(delays) + sum(rechecks)


class SalesBackfill:
    """Reads a list update's last sales again until the warehouse answers."""

    def __init__(
        self,
        repository: QuotationRepository,
        warehouse: WarehouseSource,
        *,
        delays: Sequence[float] = DELAYS,
        timeout: float = READ_TIMEOUT,
        rechecks: Sequence[float] = RECHECKS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Create it.

        Args:
            repository (QuotationRepository): Where the lines are written.
            warehouse (WarehouseSource): Where the last sales are read.
            delays (Sequence[float]): Seconds before each try.
            timeout (float): Seconds each read may take.
            rechecks (Sequence[float]): Seconds after a write to check the warning is gone.
            sleep (Callable[[float], Awaitable[None]]): Waits, for tests.
        """

        self.repository = repository
        self.warehouse = warehouse
        self.delays = tuple(delays)
        self.timeout = timeout
        self.rechecks = tuple(rechecks)
        self.sleep = sleep

    @staticmethod
    def running(quotation_id: UUID) -> bool:
        """Whether a retry of this list update is on."""

        task = _RUNNING.get(quotation_id)
        return task is not None and not task.done()

    def start(
        self, quotation_id: UUID, skus: Sequence[str], sale_keys: Sequence[str] | None
    ) -> bool:
        """Start retrying in the background, unless one is already on for it.

        Returns:
            bool: Whether a new retry started.
        """

        if not skus or self.running(quotation_id):
            return False
        task = asyncio.create_task(
            self.run(quotation_id, tuple(skus), None if sale_keys is None else tuple(sale_keys)),
            name=f"list-sales-{quotation_id}",
        )
        _RUNNING[quotation_id] = task
        task.add_done_callback(lambda _: _RUNNING.pop(quotation_id, None))
        return True

    async def run(
        self, quotation_id: UUID, skus: Sequence[str], sale_keys: Sequence[str] | None
    ) -> bool:
        """Try the read after each delay; on the first answer, write it.

        Returns:
            bool: Whether the sales were written and the warning dropped.
        """

        try:
            for delay in self.delays:
                await self.sleep(delay)
                if not await self.repository.has_warning(quotation_id, vocabulary.SALES_NOT_READ):
                    return False  # a refresh read them, or the list update is gone
                try:
                    sales = await self.warehouse.list_last_sales(
                        skus, vocabulary.LIST_UPDATE_WINDOW, sale_keys, timeout=self.timeout
                    )
                except DependencyUnavailableError as exc:
                    logger.info(
                        "quotations: last sales of %s still not read: %s", quotation_id, exc
                    )
                    continue
                return await self._write(quotation_id, skus, sales)
            logger.warning("quotations: last sales of %s not read after retrying", quotation_id)
            return False
        except Exception:  # a background task must never die silently
            logger.exception("quotations: retrying the last sales of %s failed", quotation_id)
            return False

    async def _write(self, quotation_id: UUID, skus: Sequence[str], sales) -> bool:
        # Every SKU is written: one without a sale in the window says so (empty columns).
        by_sku = {sku: rules.list_sale_fields(sales.get(sku)) for sku in skus}
        written = await self.repository.fill_last_sales(
            quotation_id, by_sku, vocabulary.SALES_NOT_READ
        )
        if not written:
            return False
        for delay in self.rechecks:
            await self.sleep(delay)
            if await self.repository.has_warning(quotation_id, vocabulary.SALES_NOT_READ):
                await self.repository.fill_last_sales(
                    quotation_id, by_sku, vocabulary.SALES_NOT_READ
                )
        logger.info("quotations: last sales of %s read on retry", quotation_id)
        return True


__all__ = ["DELAYS", "READ_TIMEOUT", "SalesBackfill", "longest_seconds"]
