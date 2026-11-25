"""Use cases of the price increase history: read, analyze and export it, upload the area's
workbook, set SIESA's date.

The suppliers' increases approved in Costos land in the history by themselves, and SIESA loads
date them (:mod:`.siesa_loads`). While the area still keeps the workbook in a sheet, it exports
the history in that format, edits it and uploads it back, and marks increases as loaded in SIESA
without a load (2026-11-25). Reading and exporting are open to whoever reads the commercial
masters (the router's gate). Uploading and changing the system update date need the history's
manager permission (``maestros`` in BI or ``acumulado-alzas`` in Costos).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.errors import DependencyUnavailableError, ForbiddenError, InvalidInputError
from app.modules.commercial_masters.domain.ports import ManagerPolicy, PlacementSource
from app.modules.commercial_masters.domain.price_increase_analysis import (
    MOVEMENTS,
    PERIODS,
    SORTS,
    AnalysisRow,
    AnalysisSummary,
    ReferenceAnalysis,
    analyze,
    in_period,
    matches,
    ordered,
    period_start,
    summarize,
)
from app.modules.commercial_masters.domain.price_increases import (
    EARLIEST_DATE,
    ORIGIN_FIELDS,
    STATUSES,
    FileRow,
    FilterOptions,
    PriceIncrease,
    PriceIncreaseFilters,
    PriceIncreasePage,
    PriceIncreaseValues,
    Scope,
    UploadIssue,
    UploadReport,
    place_options,
    scope_of,
)
from app.modules.commercial_masters.infrastructure.price_increases_file import (
    PriceIncreaseFileReader,
    history_workbook,
)
from app.modules.commercial_masters.infrastructure.price_increases_repository import (
    SqlAlchemyPriceIncreaseRepository,
)
from app.modules.identity.domain.principal import Principal

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 100
MAX_REPORTED_ISSUES = 200
# The most increases dated at once without a load (the same as a SIESA load).
MAX_DATED_AT_ONCE = 2000
# Windows kept per data version: the presets plus the months and ranges people look at.
MAX_CACHED_WINDOWS = 32
_BOGOTA = ZoneInfo("America/Bogota")

Window = tuple[date | None, date | None]


class AnalysisCache:
    """The history and its analysis by period, kept across requests (one instance per
    process): the service is built per request, the cache is not.

    It holds one data version: ``key`` is (today, rows, last change) and ``periods`` maps a
    window (since, until, operation center, installation) to its rows and analysis, worked
    out the first time it is asked.
    The oldest window goes when more than ``MAX_CACHED_WINDOWS`` are kept.
    """

    def __init__(self) -> None:
        self.key: tuple | None = None
        self.rows: list[AnalysisRow] = []
        self.periods: dict[tuple, tuple[list[AnalysisRow], list[ReferenceAnalysis]]] = {}


@dataclass(frozen=True)
class AnalysisPage:
    """A page of the references of a period, with the figures of the period."""

    items: tuple[ReferenceAnalysis, ...]
    total: int
    page: int
    size: int
    summary: AnalysisSummary
    # First and last day of the period; ``None`` is an open end (the whole history has both).
    since: date | None
    until: date | None


def numbered(rows: Sequence[FileRow]) -> dict[str, FileRow]:
    """The workbook's rows by key, none lost.

    The workbook chains increases with the same reference and dates (sku 15143 on 2021-09-24:
    15,519 to 15,984.36, then to 16,463.89). Keeping only the last of a key lost 101 such
    rows (2026-11-24), so each one is the n-th ``occurrence`` of its key, in the file's order.
    A row equal to an earlier one of its key, value for value, was typed twice and counts once.
    The export writes the rows of a key in that order, so a round trip keeps them.
    """

    by_key: dict[str, FileRow] = {}
    seen: dict[str, list[tuple]] = {}
    for row in rows:
        group = seen.setdefault(row.values.key, [])
        values = row.values.comparable()
        if values in group:
            continue
        group.append(values)
        numbered_row = replace(row, values=replace(row.values, occurrence=len(group)))
        by_key[numbered_row.values.key] = numbered_row
    return by_key


@dataclass(frozen=True)
class SystemDateResult:
    """How many picked increases got SIESA's date, and how many were left as they were
    (already dated, or in an open SIESA load)."""

    updated: int
    skipped: int


class PriceIncreaseService:
    """The price increase history of the products."""

    def __init__(
        self,
        repository: SqlAlchemyPriceIncreaseRepository,
        managers: ManagerPolicy,
        today: Callable[[], date] = lambda: datetime.now(_BOGOTA).date(),
        cache: AnalysisCache | None = None,
        placements: PlacementSource | None = None,
        reader: PriceIncreaseFileReader | None = None,
        max_upload_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        """Create the service.

        Args:
            repository (SqlAlchemyPriceIncreaseRepository): Stored rows.
            managers (ManagerPolicy): Who manages the history (BI's masters, Costos'
                ``acumulado-alzas``).
            today (Callable[[], date]): Today in Bogotá, where a period ends.
            cache (AnalysisCache | None): The process-wide analysis cache.
            placements (PlacementSource | None): Where each reference is set up (the
                warehouse's inventory), for the operation center and installation filters.
            reader (PriceIncreaseFileReader | None): Reads the area's workbook.
            max_upload_bytes (int): Largest workbook accepted.
        """

        self.repository = repository
        self.managers = managers
        self.today = today
        self.cache = cache or AnalysisCache()
        self.placements = placements
        self.reader = reader or PriceIncreaseFileReader()
        self.max_upload_bytes = max_upload_bytes

    async def scope(self, co: str | None, installation: str | None) -> Scope | None:
        """The rows an operation center or an installation keeps (:class:`Scope`); ``None``
        without either.

        Raises:
            DependencyUnavailableError: The warehouse did not answer.
        """

        co = (co or "").strip() or None
        installation = (installation or "").strip() or None
        if co is None and installation is None:
            return None
        if self.placements is None:
            raise DependencyUnavailableError("El filtro por C.O. o instalación no está disponible")
        return scope_of(await self.placements.placements(), co, installation)

    async def can_manage(self, principal: Principal) -> bool:
        return await self.managers.is_manager(principal)

    async def _require_manager(self, principal: Principal) -> None:
        if not await self.can_manage(principal):
            raise ForbiddenError("No tienes permiso para actualizar el histórico de alzas")

    async def page(
        self,
        filters: PriceIncreaseFilters,
        page: int,
        size: int,
        co: str | None = None,
        installation: str | None = None,
    ) -> PriceIncreasePage:
        """A page of the history, the latest increase first, with the counts of the filter.

        ``co`` and ``installation`` keep the rows that hold there (:class:`Scope`): a
        reference's detail then shows its national rows and those of that regional.

        Raises:
            InvalidInputError: Bad page or unknown status.
            DependencyUnavailableError: A center or installation and the warehouse is down.
        """

        if page < 1 or not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"Página inválida; el tamaño máximo es {MAX_PAGE_SIZE}")
        if filters.status is not None and filters.status not in STATUSES:
            raise InvalidInputError("Estado desconocido: subio, bajo o estable")
        scope = await self.scope(co, installation)
        if scope is not None:
            filters = replace(filters, scope=scope)
        return await self.repository.page(filters, page, size)

    async def filter_options(self) -> FilterOptions:
        """The suppliers with references in the history, the months with increases and the
        operation centers and installations with active references: what the dropdowns of
        the history and the analysis offer. Without the warehouse the last two come empty."""

        options = await self.repository.filter_options()
        if self.placements is None:
            return options
        try:
            cos, installations = place_options(await self.placements.placements())
        except DependencyUnavailableError:
            logger.warning("Price increase options without centers: the warehouse is down")
            return options
        return replace(options, cos=cos, installations=installations)

    async def analysis(
        self,
        *,
        period: str,
        q: str | None,
        movement: str | None,
        sort: str,
        descending: bool,
        page: int,
        size: int,
        since: date | None = None,
        until: date | None = None,
        supplier: str | None = None,
        co: str | None = None,
        installation: str | None = None,
    ) -> AnalysisPage:
        """The references of a period with how much they moved, the figures of the period
        and the counts by movement (of the search, before the movement filter).

        The period is a preset (``1m``, ``12m``, ``24m``, ``all``: up to the latest row) or,
        when ``since`` or ``until`` comes, those days (a calendar month is its first and
        last day). The rows of a period are read and analyzed once per day and data version
        (rows and last change); searching, filtering by supplier and movement, sorting and
        paging run on that result.

        ``co`` and ``installation`` analyze only the rows that hold there (:class:`Scope`):
        its references, with their national rows and those of its regional. The cost a
        reference starts and ends at is then that regional's.

        Raises:
            InvalidInputError: Unknown period, movement or sort, a range that ends before it
                starts, or a bad page.
            DependencyUnavailableError: A center or installation and the warehouse is down.
        """

        if page < 1 or not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"Página inválida; el tamaño máximo es {MAX_PAGE_SIZE}")
        if period not in PERIODS:
            raise InvalidInputError("Periodo desconocido: 1m, 12m, 24m o all")
        if since and until and since > until:
            raise InvalidInputError("La fecha inicial es posterior a la final")
        if movement is not None and movement not in MOVEMENTS:
            raise InvalidInputError("Movimiento desconocido: subio, bajo o estable")
        if sort not in SORTS:
            raise InvalidInputError("Orden desconocido")
        window: Window = (
            (since, until) if since or until else (period_start(period, self.today()), None)
        )
        scope = await self.scope(co, installation)
        rows, analyses = await self._analyzed(window, scope, co, installation)
        searched = [item for item in analyses if matches(item, q, None, supplier)]
        if q or supplier:
            skus = {item.sku for item in searched}
            rows = [row for row in rows if row.sku in skus]
        summary = summarize(rows, searched)
        chosen = [item for item in searched if matches(item, None, movement)]
        listed = ordered(chosen, sort, descending)
        start = (page - 1) * size
        return AnalysisPage(
            items=tuple(listed[start : start + size]),
            total=len(listed),
            page=page,
            size=size,
            summary=summary,
            since=window[0],
            until=window[1],
        )

    async def _analyzed(
        self,
        window: Window,
        scope: Scope | None = None,
        co: str | None = None,
        installation: str | None = None,
    ) -> tuple[list[AnalysisRow], list[ReferenceAnalysis]]:
        """The window's rows and analysis, read once per day and data version, for the whole
        history or the rows of a center or installation."""

        cache = self.cache
        key = (self.today(), await self.repository.version())
        if cache.key != key:
            cache.rows = await self.repository.analysis_rows()
            cache.periods = {}
            cache.key = key
        where = (
            (None, None)
            if scope is None
            else ((co or "").strip() or None, (installation or "").strip() or None)
        )
        cached = (*window, *where)
        if cached not in cache.periods:
            since, until = window
            rows = (
                cache.rows
                if scope is None
                else [row for row in cache.rows if scope.holds(row.sku, row.regional)]
            )
            # Grouping 50 000 rows is CPU work: in a thread, it never holds up the API.
            analyses = await asyncio.to_thread(analyze, rows, since, until)
            if len(cache.periods) >= MAX_CACHED_WINDOWS:
                cache.periods.pop(next(iter(cache.periods)))
            cache.periods[cached] = (in_period(rows, since, until), analyses)
        return cache.periods[cached]

    async def upload(
        self, principal: Principal, filename: str | None, content: bytes, *, apply: bool
    ) -> UploadReport:
        """Upsert the area's workbook by reference, increase and notification dates and
        regional (the export, edited in the sheet, comes back this way).

        Rows with the same key are increases in a row and each one is kept (:func:`numbered`);
        only a row typed twice, value for value, counts once. A row that came from a supplier's
        request keeps that origin: the workbook updates its values, never where it came from.
        Rows of references the product master does not have are kept too and counted. With
        ``apply`` false nothing is written.

        Raises:
            ForbiddenError: The caller does not manage the history.
            InvalidInputError: Empty, too large or unreadable file.
        """

        await self._require_manager(principal)
        if not content:
            raise InvalidInputError("El archivo está vacío")
        if len(content) > self.max_upload_bytes:
            megas = self.max_upload_bytes // (1024 * 1024)
            raise InvalidInputError(f"El archivo pasa de {megas} MB")
        # openpyxl is synchronous: in a thread, a 50 000-row file never holds up the API.
        rows = await asyncio.to_thread(self.reader.read, filename, content)
        by_key = numbered(rows)
        duplicated = len(rows) - len(by_key)
        stored = await self.repository.existing(list(by_key))
        known = await self.repository.known_skus(sorted({row.values.sku for row in rows}))
        inserted: list[PriceIncreaseValues] = []
        updated: list[PriceIncreaseValues] = []
        for key, row in by_key.items():
            current = stored.get(key)
            if current is None:
                inserted.append(row.values)
                continue
            values = replace(row.values, **{name: getattr(current, name) for name in ORIGIN_FIELDS})
            if current.comparable() != values.comparable():
                updated.append(values)
        issues = [
            UploadIssue(row.row_number, row.values.sku, problem)
            for row in rows
            for problem in row.problems
        ]
        if apply:
            await self.repository.upsert([*inserted, *updated], principal.email, datetime.now(UTC))
        return UploadReport(
            applied=apply,
            rows_read=len(rows),
            inserted=len(inserted),
            updated=len(updated),
            unchanged=len(by_key) - len(inserted) - len(updated),
            duplicated=duplicated,
            unknown_skus=len({row.values.sku for row in rows} - known),
            pending=sum(1 for row in by_key.values() if row.values.system_updated_on is None),
            issues=tuple(issues[:MAX_REPORTED_ISSUES]),
            issues_truncated=len(issues) > MAX_REPORTED_ISSUES,
        )

    async def export(
        self,
        filters: PriceIncreaseFilters,
        co: str | None = None,
        installation: str | None = None,
    ) -> tuple[str, bytes]:
        """The history of the filter as the area's workbook («Acumulado Alza Precios»), ready
        to edit in the sheet and upload back.

        Raises:
            DependencyUnavailableError: A center or installation and the warehouse is down.
        """

        scope = await self.scope(co, installation)
        if scope is not None:
            filters = replace(filters, scope=scope)
        rows = await self.repository.export_rows(filters)
        content = await asyncio.to_thread(history_workbook, rows)
        return f"Acumulado Alza Precios {self.today():%d-%m-%Y}.xlsx", content

    async def mark_loaded(
        self,
        principal: Principal,
        day: date,
        increase_ids: Sequence[UUID] = (),
        *,
        all: bool = False,
        filters: PriceIncreaseFilters | None = None,
        co: str | None = None,
        installation: str | None = None,
    ) -> SystemDateResult:
        """Give SIESA's date to pending increases without a SIESA load (the area loaded them
        by itself): these ids, or every pending one of the filters (``all``). Those already
        dated or in an open load are left as they are (a load is confirmed or voided whole).

        Raises:
            ForbiddenError: The caller does not manage the history.
            InvalidInputError: A day in the future or before 2000, nothing picked or too many.
            DependencyUnavailableError: A center or installation and the warehouse is down.
        """

        await self._require_manager(principal)
        if day < EARLIEST_DATE or day > self.today():
            raise InvalidInputError("La fecha de actualización debe ser de hoy o anterior")
        if all:
            chosen = replace(filters or PriceIncreaseFilters(), pending=True)
            scope = await self.scope(co, installation)
            if scope is not None:
                chosen = replace(chosen, scope=scope)
            ids = await self.repository.ids_for(chosen, MAX_DATED_AT_ONCE + 1)
        else:
            ids = list(dict.fromkeys(increase_ids))
        if not ids:
            raise InvalidInputError("Elige al menos un alza pendiente")
        if len(ids) > MAX_DATED_AT_ONCE:
            raise InvalidInputError(
                f"Se marcan hasta {MAX_DATED_AT_ONCE} alzas a la vez; filtra un poco más"
            )
        updated = await self.repository.mark_loaded(ids, day, principal.email, datetime.now(UTC))
        return SystemDateResult(updated=updated, skipped=len(ids) - updated)

    async def set_system_date(
        self, principal: Principal, increase_id: UUID, day: date | None
    ) -> PriceIncrease:
        """Store the day SIESA got the increase; ``None`` marks it pending again.

        Raises:
            ForbiddenError: The caller does not manage the masters.
            InvalidInputError: A day before 2000.
            NotFoundError: The row does not exist.
        """

        await self._require_manager(principal)
        if day is not None and day < EARLIEST_DATE:
            raise InvalidInputError("La fecha de actualización es anterior al año 2000")
        await self.repository.set_system_date(increase_id, day, principal.email, datetime.now(UTC))
        return await self.repository.get(increase_id)


__all__ = ["MAX_DATED_AT_ONCE", "MAX_PAGE_SIZE", "PriceIncreaseService", "SystemDateResult"]
