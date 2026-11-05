"""Copy every declared master from the data warehouse into ``master_records``.

The masters and their columns come from ``master_definitions`` / ``master_fields``, so
a new master needs a migration that declares it, not new code. Full syncs only, like
the commercial masters: SIESA's timestamps do not mark real changes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from app.core.errors import InvalidInputError
from app.modules.masters.application.mapping import record_row
from app.modules.masters.domain.entities import MasterDetail, MasterSyncReport, build_spec
from app.modules.masters.domain.ports import MasterSource, MasterSyncRepository

logger = logging.getLogger(__name__)

_ERROR_LENGTH = 500


class MasterCatalogSync:
    """Read each active master in pages and upsert it by its key.

    A master that fails (a view missing, no permission to read it) is reported and
    the others go on. A row that disappears from the warehouse is deactivated, never
    deleted, and only after a non-empty read: an empty answer must not wipe a master.
    """

    def __init__(
        self,
        source: MasterSource,
        repository: MasterSyncRepository,
        batch_size: int = 2000,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Create the service.

        Args:
            source (MasterSource): Read-only warehouse access.
            repository (MasterSyncRepository): Local record persistence.
            batch_size (int): Rows per warehouse page and per upsert.
            clock (Callable[[], datetime] | None): Current instant, for tests.
        """

        self.source = source
        self.repository = repository
        self.batch_size = batch_size
        self.clock = clock or (lambda: datetime.now(UTC))

    async def run(
        self, only: Sequence[str] | None = None, apply: bool = True
    ) -> list[MasterSyncReport]:
        """Sync the selected masters in their display order.

        Args:
            only (Sequence[str] | None): Master slugs; every active master when ``None``.
            apply (bool): Write to the platform; ``False`` only reads and counts.

        Returns:
            list[MasterSyncReport]: One report per master.

        Raises:
            InvalidInputError: When a slug is not an active master.
        """

        definitions = await self.repository.list_definitions()
        known = {definition.summary.slug for definition in definitions}
        unknown = sorted(set(only or ()) - known)
        if unknown:
            raise InvalidInputError(f"Maestros desconocidos o inactivos: {', '.join(unknown)}")
        return [
            await self._sync(definition, apply)
            for definition in definitions
            if not only or definition.summary.slug in only
        ]

    async def _sync(self, detail: MasterDetail, apply: bool) -> MasterSyncReport:
        report = MasterSyncReport(slug=detail.summary.slug, started_at=self.clock())
        try:
            spec = build_spec(detail)
            async for page in self.source.records(spec, self.batch_size):
                report.read += len(page)
                rows = [mapped for row in page if (mapped := record_row(spec, row)) is not None]
                if apply and rows:
                    report.upserted += await self.repository.upsert_records(
                        spec.master_id, rows, report.started_at
                    )
                else:
                    report.upserted += len(rows)
            if apply and report.read:
                report.deactivated = await self.repository.deactivate_unseen(
                    spec.master_id, report.started_at
                )
                await self.repository.touch_definition(spec.master_id, report.started_at)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            report.error = error_message(exc)
            logger.exception("Master sync: %s failed", report.slug)
        report.finished_at = self.clock()
        return report


def error_message(exc: BaseException) -> str:
    """The error and its cause (the warehouse wraps the driver error), shortened."""

    message = str(exc) or type(exc).__name__
    cause = exc.__cause__
    if cause is not None and str(cause) and str(cause) not in message:
        message = f"{message}: {cause}"
    return message[:_ERROR_LENGTH]
