"""Copy the warehouse ``monitoreo`` views into the platform, one cycle at a time."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.modules.dwh_monitoring.domain.entities import LoadCycle, ViewReport
from app.modules.dwh_monitoring.domain.ports import MonitoringCopyRepository, MonitoringSource, Row
from app.modules.dwh_monitoring.domain.views import VIEWS, Kind, LoadMode, ViewSpec

logger = logging.getLogger(__name__)

# Keys re-read per query when refreshing rows that were still open.
_KEYS_PER_QUERY = 500


def to_local(view: ViewSpec, row: Row) -> dict[str, Any]:
    """Map a warehouse row to the local column names and types.

    Args:
        view (ViewSpec): The view the row comes from.
        row (Row): Row as the driver returns it.

    Returns:
        dict[str, Any]: Row keyed by local column name.
    """

    local: dict[str, Any] = {}
    for column in view.columns:
        value = row.get(column.source)
        if value is not None and column.kind is Kind.INTERVAL:
            value = round(value.total_seconds()) if isinstance(value, timedelta) else int(value)
        elif isinstance(value, str) and column.kind is Kind.JSON:
            value = json.loads(value)
        local[column.local] = value
    return local


class MonitoringSyncService:
    """Run load cycles: snapshots replaced whole, run history read incrementally.

    An incremental view is read from its newest local ``inicio`` minus ``lookback``,
    so a run that was still going on the previous cycle is read again. A run older
    than the window that is still open locally (``running`` or without an end) is
    re-read by key, so it never stays frozen. A failed view keeps its last copy and
    the cycle is marked ``partial``; an empty answer never wipes a snapshot.
    """

    def __init__(
        self,
        source: MonitoringSource,
        repository: MonitoringCopyRepository,
        lookback: timedelta = timedelta(days=2),
        backfill: timedelta = timedelta(days=90),
        batch_size: int = 2000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create the service.

        Args:
            source (MonitoringSource): Read-only warehouse access.
            repository (MonitoringCopyRepository): Local copy and cycle log.
            lookback (timedelta): How far behind the newest local run each read starts.
            backfill (timedelta): History taken on the first load of an incremental view.
            batch_size (int): Rows per warehouse page and per upsert.
            clock (Callable[[], datetime]): Current time, replaceable in tests.
        """

        self.source = source
        self.repository = repository
        self.lookback = lookback
        self.backfill = backfill
        self.batch_size = batch_size
        self.clock = clock

    async def run(self, only: Sequence[str] | None = None) -> LoadCycle:
        """Run one cycle and log it.

        Args:
            only (Sequence[str] | None): View names to load; every view when ``None``.

        Returns:
            LoadCycle: The closed cycle with one report per view.
        """

        cycle = LoadCycle(id=uuid4(), started_at=self.clock())
        await self.repository.save_cycle(cycle)
        try:
            await self.source.check()
        except Exception as exc:
            cycle.error = f"Sin conexión con el datawarehouse: {exc}"
            logger.warning("DWH monitoring: %s", cycle.error)
        else:
            for view in VIEWS:
                if only and view.name not in only:
                    continue
                cycle.views[view.name] = await self._load(view, cycle.started_at)
        cycle.close(self.clock())
        await self.repository.save_cycle(cycle)
        return cycle

    async def _load(self, view: ViewSpec, loaded_at: datetime) -> ViewReport:
        report = ViewReport()
        try:
            if view.mode is LoadMode.INCREMENTAL:
                await self._incremental(view, loaded_at, report)
            else:
                rows = [to_local(view, row) for row in await self.source.read_all(view)]
                report.read = len(rows)
                if view.mode is LoadMode.HISTORY:
                    report.written = await self.repository.append(view, rows, loaded_at)
                elif rows:
                    report.written = await self.repository.replace(view, rows, loaded_at)
        except Exception as exc:
            report.error = str(exc) or type(exc).__name__
            logger.exception("DWH monitoring: loading %s failed", view.name)
        return report

    async def _incremental(self, view: ViewSpec, loaded_at: datetime, report: ViewReport) -> None:
        latest = await self.repository.latest_start(view)
        since = (latest - self.lookback) if latest else (loaded_at - self.backfill)
        async for page in self.source.read_since(view, since, self.batch_size):
            rows = [to_local(view, row) for row in page]
            report.read += len(rows)
            report.written += await self.repository.upsert(view, rows, loaded_at)
        stale = await self.repository.open_keys(view, loaded_at)
        for start in range(0, len(stale), _KEYS_PER_QUERY):
            found = await self.source.read_keys(view, stale[start : start + _KEYS_PER_QUERY])
            rows = [to_local(view, row) for row in found]
            report.read += len(rows)
            report.written += await self.repository.upsert(view, rows, loaded_at)
