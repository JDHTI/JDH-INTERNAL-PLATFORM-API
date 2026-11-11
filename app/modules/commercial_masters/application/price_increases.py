"""Use cases of the price increase history: read it, upload the workbook, set SIESA's date.

Reading is open to whoever reads the commercial masters (the router's gate). Uploading and
changing the system update date need the masters' manager permission (BI, module
``maestros``). An upload is a simulation unless ``apply``: it says what it would insert,
update and leave as it is, and what it could not read, before anything is written.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.errors import ForbiddenError, InvalidInputError
from app.modules.commercial_masters.domain.ports import ManagerPolicy
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
    STATUSES,
    FileRow,
    PriceIncrease,
    PriceIncreaseFilters,
    PriceIncreasePage,
    PriceIncreaseValues,
    UploadIssue,
    UploadReport,
)
from app.modules.commercial_masters.infrastructure.price_increases_file import (
    PriceIncreaseFileReader,
)
from app.modules.commercial_masters.infrastructure.price_increases_repository import (
    SqlAlchemyPriceIncreaseRepository,
)
from app.modules.identity.domain.principal import Principal

MAX_PAGE_SIZE = 100
MAX_REPORTED_ISSUES = 200
_BOGOTA = ZoneInfo("America/Bogota")


class AnalysisCache:
    """The history and its analysis by period, kept across requests (one instance per
    process): the service is built per request, the cache is not.

    It holds one data version: ``key`` is (today, rows, last change) and ``periods`` maps a
    period to its rows and analysis, worked out the first time it is asked.
    """

    def __init__(self) -> None:
        self.key: tuple | None = None
        self.rows: list[AnalysisRow] = []
        self.periods: dict[str, tuple[list[AnalysisRow], list[ReferenceAnalysis]]] = {}


@dataclass(frozen=True)
class AnalysisPage:
    """A page of the references of a period, with the figures of the period."""

    items: tuple[ReferenceAnalysis, ...]
    total: int
    page: int
    size: int
    summary: AnalysisSummary
    # First day of the period; ``None`` for the whole history.
    since: date | None


class PriceIncreaseService:
    """The price increase history of the products."""

    def __init__(
        self,
        repository: SqlAlchemyPriceIncreaseRepository,
        managers: ManagerPolicy,
        reader: PriceIncreaseFileReader,
        max_upload_bytes: int,
        today: Callable[[], date] = lambda: datetime.now(_BOGOTA).date(),
        cache: AnalysisCache | None = None,
    ) -> None:
        """Create the service.

        Args:
            repository (SqlAlchemyPriceIncreaseRepository): Stored rows.
            managers (ManagerPolicy): Who manages the commercial masters.
            reader (PriceIncreaseFileReader): Reads the workbook.
            max_upload_bytes (int): Largest workbook accepted.
            today (Callable[[], date]): Today in Bogotá, where a period ends.
            cache (AnalysisCache | None): The process-wide analysis cache.
        """

        self.repository = repository
        self.managers = managers
        self.reader = reader
        self.max_upload_bytes = max_upload_bytes
        self.today = today
        self.cache = cache or AnalysisCache()

    async def can_manage(self, principal: Principal) -> bool:
        return await self.managers.is_manager(principal)

    async def _require_manager(self, principal: Principal) -> None:
        if not await self.can_manage(principal):
            raise ForbiddenError("No tienes permiso para actualizar el histórico de alzas")

    async def page(self, filters: PriceIncreaseFilters, page: int, size: int) -> PriceIncreasePage:
        """A page of the history, the latest increase first, with the counts of the filter."""

        if page < 1 or not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"Página inválida; el tamaño máximo es {MAX_PAGE_SIZE}")
        if filters.status is not None and filters.status not in STATUSES:
            raise InvalidInputError("Estado desconocido: subio, bajo o estable")
        return await self.repository.page(filters, page, size)

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
    ) -> AnalysisPage:
        """The references of a period with how much they moved, the figures of the period
        and the counts by movement (of the search, before the movement filter).

        The rows of a period are read and analyzed once per day and data version (rows and
        last change); filtering, sorting and paging run on that result.

        Raises:
            InvalidInputError: Unknown period, movement or sort, or a bad page.
        """

        if page < 1 or not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"Página inválida; el tamaño máximo es {MAX_PAGE_SIZE}")
        if period not in PERIODS:
            raise InvalidInputError("Periodo desconocido: 12m, 24m o all")
        if movement is not None and movement not in MOVEMENTS:
            raise InvalidInputError("Movimiento desconocido: subio, bajo o estable")
        if sort not in SORTS:
            raise InvalidInputError("Orden desconocido")
        rows, analyses = await self._analyzed(period)
        searched = [item for item in analyses if matches(item, q, None)]
        if q:
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
            since=period_start(period, self.today()),
        )

    async def _analyzed(self, period: str) -> tuple[list[AnalysisRow], list[ReferenceAnalysis]]:
        """The period's rows and analysis, read once per day and data version."""

        today = self.today()
        cache = self.cache
        key = (today, await self.repository.version())
        if cache.key != key:
            cache.rows = await self.repository.analysis_rows()
            cache.periods = {}
            cache.key = key
        if period not in cache.periods:
            since = period_start(period, today)
            # Grouping 50 000 rows is CPU work: in a thread, it never holds up the API.
            analyses = await asyncio.to_thread(analyze, cache.rows, since)
            cache.periods[period] = (in_period(cache.rows, since), analyses)
        return cache.periods[period]

    async def upload(
        self, principal: Principal, filename: str | None, content: bytes, *, apply: bool
    ) -> UploadReport:
        """Upsert the workbook's rows by reference, increase date and notification date.

        A key repeated in the file keeps its last row. Rows of references the product master
        does not have are kept too (the history outlives the products) and counted. With
        ``apply`` false nothing is written.

        Raises:
            ForbiddenError: The caller does not manage the masters.
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
        by_key: dict[str, FileRow] = {}
        for row in rows:
            by_key[row.values.key] = row
        duplicated = len(rows) - len(by_key)
        stored = await self.repository.existing(list(by_key))
        known = await self.repository.known_skus(sorted({row.values.sku for row in rows}))
        inserted: list[PriceIncreaseValues] = []
        updated: list[PriceIncreaseValues] = []
        for key, row in by_key.items():
            current = stored.get(key)
            if current is None:
                inserted.append(row.values)
            elif current.comparable() != row.values.comparable():
                updated.append(row.values)
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


__all__ = ["MAX_PAGE_SIZE", "PriceIncreaseService"]
