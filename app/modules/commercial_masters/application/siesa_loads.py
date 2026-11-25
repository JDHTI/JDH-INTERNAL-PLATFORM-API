"""Use cases of the SIESA loads: generate the quotation file of pending increases, confirm
it was loaded, or void it (see :mod:`app.modules.commercial_masters.domain.siesa_loads`).

Whoever manages the history of increases generates, confirms and voids (``maestros`` in BI or
``acumulado-alzas`` in Costos); whoever reads it lists the loads and downloads their files.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.errors import ForbiddenError, InvalidInputError, NotFoundError
from app.modules.commercial_masters.application.price_increases import PriceIncreaseService
from app.modules.commercial_masters.domain.price_increases import (
    EARLIEST_DATE,
    PriceIncreaseFilters,
)
from app.modules.commercial_masters.domain.siesa_loads import (
    LOAD_STATUSES,
    MAX_LOAD_ROWS,
    CurrentQuotation,
    LoadPage,
    LoadPlan,
    SiesaLoad,
    file_name,
    plan,
)
from app.modules.commercial_masters.infrastructure.siesa_load_files import (
    quotation_file,
    quotation_workbook,
)
from app.modules.commercial_masters.infrastructure.siesa_load_repository import (
    SqlAlchemySiesaLoadRepository,
)
from app.modules.identity.domain.principal import Principal

MAX_PAGE_SIZE = 100
_BOGOTA = ZoneInfo("America/Bogota")
_TXT = "text/plain; charset=us-ascii"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class QuotationSource(Protocol):
    async def current(self, skus: Sequence[str]) -> dict[str, CurrentQuotation]: ...


@dataclass(frozen=True)
class Selection:
    """The increases to load: these ids, or every pending one of a filter (``all``)."""

    increase_ids: tuple[UUID, ...] = ()
    all: bool = False
    filters: PriceIncreaseFilters = PriceIncreaseFilters()
    co: str | None = None
    installation: str | None = None


@dataclass(frozen=True)
class LoadResult:
    """What a selection loads (or would), what it leaves out and the load, when stored."""

    plan: LoadPlan
    load: SiesaLoad | None = None


@dataclass(frozen=True)
class LoadFile:
    name: str
    content_type: str
    content: bytes


class SiesaLoadService:
    """Pending increases into SIESA's supplier quotations."""

    def __init__(
        self,
        repository: SqlAlchemySiesaLoadRepository,
        increases: PriceIncreaseService,
        quotations: QuotationSource,
        today: Callable[[], date] = lambda: datetime.now(_BOGOTA).date(),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create the service.

        Args:
            repository (SqlAlchemySiesaLoadRepository): The loads.
            increases (PriceIncreaseService): The history (rows, filters and its managers).
            quotations (QuotationSource): Each reference's quotation in force (the warehouse).
            today (Callable[[], date]): Today in Bogotá.
            clock (Callable[[], datetime]): Now, in UTC.
        """

        self.repository = repository
        self.increases = increases
        self.quotations = quotations
        self.today = today
        self.clock = clock

    async def _require_manager(self, principal: Principal) -> None:
        if not await self.increases.can_manage(principal):
            raise ForbiddenError("No tienes permiso para cargar alzas en SIESA")

    async def _picked(self, selection: Selection):
        if selection.all:
            filters = replace(selection.filters, pending=True)
            scope = await self.increases.scope(selection.co, selection.installation)
            if scope is not None:
                filters = replace(filters, scope=scope)
            ids = await self.increases.repository.ids_for(filters, MAX_LOAD_ROWS + 1)
        else:
            ids = list(dict.fromkeys(selection.increase_ids))
        if not ids:
            raise InvalidInputError("Elige al menos un alza pendiente")
        if len(ids) > MAX_LOAD_ROWS:
            raise InvalidInputError(
                f"Un lote lleva hasta {MAX_LOAD_ROWS} alzas; filtra un poco más la selección"
            )
        return await self.increases.repository.by_ids(ids)

    async def generate(
        self, principal: Principal, selection: Selection, *, apply: bool
    ) -> LoadResult:
        """What the selection loads and leaves out; with ``apply``, store it as a load.

        Raises:
            ForbiddenError: The caller does not manage the history.
            InvalidInputError: Nothing picked, too many, or nothing that can go.
            ConflictError: An increase went into another load meanwhile.
            DependencyUnavailableError: The warehouse did not answer.
        """

        await self._require_manager(principal)
        picked = await self._picked(selection)
        quotations = await self.quotations.current(sorted({item.values.sku for item in picked}))
        open_loads = await self.repository.open_loads([item.id for item in picked])
        result = plan(picked, quotations, open_loads, self.today())
        if not apply:
            return LoadResult(result)
        if not result.rows:
            raise InvalidInputError("Ninguna de las alzas elegidas se puede cargar en SIESA")
        load = await self.repository.create(result.rows, principal.email, self.clock())
        return LoadResult(result, load)

    async def page(self, status: str | None, page: int, size: int) -> LoadPage:
        """The loads, the latest first.

        Raises:
            InvalidInputError: Unknown status or bad page.
        """

        if page < 1 or not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"Página inválida; el tamaño máximo es {MAX_PAGE_SIZE}")
        if status is not None and status not in LOAD_STATUSES:
            raise InvalidInputError("Estado desconocido: generado, cargado o anulado")
        return await self.repository.page(status, page, size)

    async def get(self, load_id: UUID) -> SiesaLoad:
        """A load with its rows.

        Raises:
            NotFoundError: No such load.
        """

        load = await self.repository.get(load_id)
        if load is None:
            raise NotFoundError("Lote no encontrado")
        return load

    async def file(self, load_id: UUID, kind: str) -> LoadFile:
        """The load's import file (``txt``) or its workbook (``xlsx``), written again from
        what was stored: the same bytes every time.

        Raises:
            NotFoundError: No such load.
            InvalidInputError: Unknown kind.
        """

        load = await self.get(load_id)
        day = load.created_at.astimezone(_BOGOTA).date()
        if kind == "txt":
            return LoadFile(file_name(day, load.number, "txt"), _TXT, quotation_file(load.rows))
        if kind == "xlsx":
            return LoadFile(
                file_name(day, load.number, "xlsx"), _XLSX, quotation_workbook(load.rows)
            )
        raise InvalidInputError("Archivo desconocido: txt o xlsx")

    async def confirm(
        self, principal: Principal, load_id: UUID, loaded_on: date | None = None
    ) -> SiesaLoad:
        """The file was loaded in SIESA: its increases get that day as their SIESA date and
        leave the pending ones (the quoter stops comparing them).

        Raises:
            ForbiddenError: The caller does not manage the history.
            InvalidInputError: A day before 2000 or after today.
            NotFoundError: No such load.
            ConflictError: Already confirmed or voided.
        """

        await self._require_manager(principal)
        day = loaded_on or self.today()
        if day < EARLIEST_DATE or day > self.today():
            raise InvalidInputError("La fecha de cargue debe ser de hoy o anterior")
        await self.repository.confirm(load_id, day, principal.email, self.clock())
        return await self.get(load_id)

    async def void(self, principal: Principal, load_id: UUID) -> SiesaLoad:
        """The file was not loaded: the load is voided and its increases are free again.

        Raises:
            ForbiddenError: The caller does not manage the history.
            NotFoundError: No such load.
            ConflictError: Already confirmed or voided.
        """

        await self._require_manager(principal)
        await self.repository.void(load_id, principal.email, self.clock())
        return await self.get(load_id)


__all__ = ["MAX_PAGE_SIZE", "LoadFile", "LoadResult", "Selection", "SiesaLoadService"]
