"""The entries analysis: the purchase entries of a period against the quotation in force."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.core.errors import DependencyUnavailableError, InvalidInputError
from app.modules.costs.domain.entries import (
    ENTRY_SORTS,
    ENTRY_STATUSES,
    NOVELTY_STATUSES,
    EntryFilters,
    EntryLine,
    EntryPage,
    EntryRow,
    FilterOptions,
    NoveltyBrief,
    Placement,
    fill_previous_prices,
    filter_options,
    with_status,
)
from app.modules.costs.domain.ports import EntrySource, EntryWindow, NoveltyRepository

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 200
DEFAULT_DAYS = 30
# Periods kept per process; the warehouse publishes the entries once a day.
MAX_CACHED_WINDOWS = 6
_BOGOTA = ZoneInfo("America/Bogota")

Window = tuple[date, date]


@dataclass
class _Cached:
    loaded: float
    fetched_at: datetime
    lines: list[EntryLine]
    previous_from_warehouse: bool


@dataclass
class EntryCache:
    """The analyzed lines of the latest periods asked, kept a few minutes (one per process:
    the service is built per request, the cache is not)."""

    windows: dict[Window, _Cached] = field(default_factory=dict)
    locks: dict[Window, asyncio.Lock] = field(default_factory=dict)


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _same_center(left: str | None, right: str) -> bool:
    """``"003"`` and ``"3"`` are the same operation center."""

    return (left or "").strip().lstrip("0") == right.strip().lstrip("0")


async def center_names(source: EntrySource) -> dict[str, str]:
    """The operation centers' SIESA names; empty when the warehouse does not answer (the
    options then fall back to the regional's name)."""

    try:
        return await source.centers()
    except DependencyUnavailableError:
        logger.warning("Costs: operation center names unavailable; using the regionals'")
        return {}


def matches(row: EntryRow, filters: EntryFilters) -> bool:
    """Whether a line passes every filter but the status ones."""

    line = row.line
    if filters.supplier and line.supplier_code != filters.supplier.strip():
        return False
    if filters.co and not _same_center(line.co, filters.co):
        return False
    if (
        filters.warehouse
        and (line.warehouse or "").strip().upper() != filters.warehouse.strip().upper()
    ):
        return False
    if filters.reason_id is not None and (
        row.novelty is None or row.novelty.reason_id != filters.reason_id
    ):
        return False
    term = _clean(filters.q)
    if term:
        lowered = term.lower()
        haystack = (
            (line.description or "").lower(),
            (line.document or "").lower(),
            (line.purchase_order or "").lower(),
            (line.invoice_ref or "").lower(),
        )
        if not line.sku.startswith(term) and not any(lowered in text for text in haystack):
            return False
    return True


def _sort_value(line: EntryLine, sort: str):
    if sort == "difference":
        return None if line.difference is None else abs(line.difference)
    if sort == "difference_pct":
        return None if line.difference_pct is None else abs(line.difference_pct)
    if sort == "amount":
        return None if line.amount is None else abs(line.amount)
    if sort == "sku":
        return (line.sku.zfill(12), line.entry_date)
    if sort == "supplier":
        return ((line.supplier_name or "").lower(), line.entry_date)
    return (line.entry_date, line.co or "", line.document or "", line.line_key)


def ordered(rows: list[EntryRow], sort: str, descending: bool) -> list[EntryRow]:
    """The rows by ``sort``; a line without the value (no quotation) always goes last.

    The differences sort by size, up or down alike: the largest variation first.
    """

    present = [row for row in rows if _sort_value(row.line, sort) is not None]
    missing = [row for row in rows if _sort_value(row.line, sort) is None]
    present.sort(key=lambda row: _sort_value(row.line, sort), reverse=descending)
    return present + missing


class EntryAnalysisService:
    """Purchase entries of a period, their status against the quotation and what the platform
    keeps of each (reason, credit note)."""

    def __init__(
        self,
        source: EntrySource,
        novelties: NoveltyRepository,
        threshold: Decimal,
        max_range_days: int,
        cache_seconds: int,
        cache: EntryCache | None = None,
        today: Callable[[], date] = lambda: datetime.now(_BOGOTA).date(),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create the service.

        Args:
            source (EntrySource): The warehouse's entries and quotations.
            novelties (NoveltyRepository): What the platform keeps of the lines.
            threshold (Decimal): Largest relative difference that is no novelty (0.001).
            max_range_days (int): Longest period one request reads.
            cache_seconds (int): How long a period's lines are kept before reading again.
            cache (EntryCache | None): The process-wide cache.
            today (Callable[[], date]): Today in Bogotá.
            monotonic (Callable[[], float]): Clock of the cache.
        """

        self.source = source
        self.novelties = novelties
        self.threshold = threshold
        self.max_range_days = max_range_days
        self.cache_seconds = cache_seconds
        self.cache = cache or EntryCache()
        self.today = today
        self.monotonic = monotonic

    def period(self, since: date | None, until: date | None) -> Window:
        """The days to read: the last 30 by default, never longer than the maximum.

        Raises:
            InvalidInputError: It ends before it starts or is too long.
        """

        end = until or self.today()
        start = since or end - timedelta(days=DEFAULT_DAYS - 1)
        if start > end:
            raise InvalidInputError("La fecha inicial es posterior a la final")
        if (end - start).days + 1 > self.max_range_days:
            raise InvalidInputError(f"El periodo no puede pasar de {self.max_range_days} días")
        return start, end

    async def _lines(self, window: Window) -> _Cached:
        cached = self.cache.windows.get(window)
        now = self.monotonic()
        if cached is not None and now - cached.loaded < self.cache_seconds:
            return cached
        lock = self.cache.locks.setdefault(window, asyncio.Lock())
        async with lock:
            cached = self.cache.windows.get(window)
            if cached is not None and self.monotonic() - cached.loaded < self.cache_seconds:
                return cached
            read: EntryWindow = await self.source.lines(*window)
            lines = with_status(read.lines, self.threshold)
            if not read.previous_from_warehouse:
                lines = fill_previous_prices(lines)
            cached = _Cached(
                self.monotonic(), datetime.now(UTC), lines, read.previous_from_warehouse
            )
            if window not in self.cache.windows and len(self.cache.windows) >= MAX_CACHED_WINDOWS:
                oldest = next(iter(self.cache.windows))
                self.cache.windows.pop(oldest)
                self.cache.locks.pop(oldest, None)
            self.cache.windows[window] = cached
            return cached

    async def options(self, since: date | None, until: date | None) -> FilterOptions:
        """The operation centers and warehouses with entries in the period (the analysis'
        dropdowns), read from the period's copy.

        Raises:
            InvalidInputError: Bad period.
            DependencyUnavailableError: The warehouse did not answer.
        """

        cached = await self._lines(self.period(since, until))
        places = (Placement(line.co, line.warehouse, line.regional_name) for line in cached.lines)
        return filter_options(places, await center_names(self.source))

    async def page(
        self,
        *,
        since: date | None,
        until: date | None,
        filters: EntryFilters,
        sort: str,
        descending: bool,
        page: int,
        size: int,
    ) -> EntryPage:
        """A page of the entries of a period with the counts of the search.

        Args:
            since (date | None): First day; 30 days before ``until`` when absent.
            until (date | None): Last day; today when absent.
            filters (EntryFilters): Search, status, supplier, center, warehouse and reason.
            sort (str): ``date``, ``difference``, ``difference_pct``, ``amount``, ``sku`` or
                ``supplier``.
            descending (bool): Largest (or latest) first.
            page (int): Page, from 1.
            size (int): Rows per page.

        Returns:
            EntryPage: The rows, the total, the counts by status and the period read.

        Raises:
            InvalidInputError: Bad period, status, sort or page.
            DependencyUnavailableError: The warehouse did not answer.
        """

        if page < 1 or not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"Página inválida; el tamaño máximo es {MAX_PAGE_SIZE}")
        if filters.status is not None and filters.status not in ENTRY_STATUSES:
            raise InvalidInputError("Estado desconocido: ok, novedad o sin_cotizacion")
        if sort not in ENTRY_SORTS:
            raise InvalidInputError("Orden desconocido")
        window = self.period(since, until)
        cached = await self._lines(window)
        briefs: dict[str, NoveltyBrief] = await self.novelties.briefs_between(*window)
        rows = [EntryRow(line, briefs.get(line.line_key)) for line in cached.lines]
        searched = [row for row in rows if matches(row, filters)]
        counts = {status: 0 for status in ENTRY_STATUSES}
        for row in searched:
            counts[row.line.status] += 1
        counts["total"] = len(searched)
        counts["with_reason"] = sum(1 for row in searched if row.novelty and row.novelty.reason_id)
        if filters.status is not None:
            chosen = [row for row in searched if row.line.status == filters.status]
        elif filters.novelty_only:
            chosen = [row for row in searched if row.line.status in NOVELTY_STATUSES]
        else:
            chosen = searched
        listed = ordered(chosen, sort, descending)
        start = (page - 1) * size
        return EntryPage(
            items=tuple(listed[start : start + size]),
            total=len(listed),
            page=page,
            size=size,
            since=window[0],
            until=window[1],
            counts=counts,
            fetched_at=cached.fetched_at,
            previous_from_warehouse=cached.previous_from_warehouse,
        )


__all__ = [
    "MAX_PAGE_SIZE",
    "EntryAnalysisService",
    "EntryCache",
    "center_names",
    "matches",
    "ordered",
]
