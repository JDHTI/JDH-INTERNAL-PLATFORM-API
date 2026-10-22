"""Read use cases of the TI area's data warehouse monitoring screens.

Everything is read from the platform's copy of ``monitoreo``, never from the
warehouse on a request: the screens keep answering while the warehouse is down,
and ``/sync`` says how old the copy is.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.dwh_monitoring.domain.entities import LoadCycle, RunFilters
from app.modules.dwh_monitoring.domain.ports import MonitoringAccess
from app.modules.identity.domain.principal import Principal

MAX_PAGE_SIZE = 500
DEFAULT_DAYS = 7

Row = dict[str, Any]


class MonitoringReader(Protocol):
    """Reads of the local copy the screens need."""

    async def sequences(self) -> list[Row]: ...
    async def sequence(self, name: str) -> Row | None: ...
    async def sequence_tables(
        self, secuencia: str | None, dominio: str | None, fase: int | None
    ) -> list[Row]: ...
    async def typical_schedule(self, secuencia: str | None = None) -> list[Row]: ...
    async def statistics(self, secuencia: str | None = None) -> list[Row]: ...
    async def freshness_latest(self) -> Row | None: ...
    async def freshness_history(self, start: datetime, end: datetime) -> list[Row]: ...
    async def freshness_domains(self) -> list[Row]: ...
    async def freshness_tables(
        self,
        estados: Sequence[str],
        dominio: str | None,
        programacion: str | None,
        query_text: str | None,
    ) -> list[Row]: ...
    async def runs(self, filters: RunFilters, page: int, size: int) -> tuple[list[Row], int]: ...
    async def runs_between(self, start: datetime, end: datetime) -> list[Row]: ...
    async def extractions(
        self, filters: RunFilters, page: int, size: int
    ) -> tuple[list[Row], int]: ...
    async def transformations(
        self, filters: RunFilters, page: int, size: int
    ) -> tuple[list[Row], int]: ...
    async def quality(self, estados: Sequence[str], tipo: str | None) -> list[Row]: ...
    async def last_cycle(self) -> LoadCycle | None: ...
    async def last_successful_load(self) -> datetime | None: ...


class DwhMonitoringService:
    """Monitoring screens for platform administrators and members of the owning area."""

    def __init__(
        self,
        repository: MonitoringReader,
        access: MonitoringAccess,
        timezone: str = "America/Bogota",
        sync_enabled: bool = False,
        sync_interval: int = 600,
        request_sync: Callable[[], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create the service.

        Args:
            repository (MonitoringReader): The local copy.
            access (MonitoringAccess): Who may read the screens.
            timezone (str): Zone of the business day (the warehouse's ``time`` columns).
            sync_enabled (bool): Whether the background load runs in this process.
            sync_interval (int): Seconds between background loads.
            request_sync (Callable[[], None] | None): Wakes the background load.
            clock (Callable[[], datetime]): Current time, replaceable in tests.
        """

        self.repository = repository
        self.access = access
        self.zone = ZoneInfo(timezone)
        self.timezone = timezone
        self.sync_enabled = sync_enabled
        self.sync_interval = sync_interval
        self.request_sync_now = request_sync
        self.clock = clock

    # --- Helpers ---

    async def _authorize(self, principal: Principal) -> None:
        if not await self.access.is_manager(principal):
            raise ForbiddenError("El monitoreo del datawarehouse es del área de TI")

    def today(self) -> date:
        """Current business day."""

        return self.clock().astimezone(self.zone).date()

    def _day_start(self, day: date) -> datetime:
        return datetime.combine(day, time.min, tzinfo=self.zone)

    def _range(self, start: date | None, end: date | None) -> tuple[datetime, datetime]:
        """Inclusive business days to ``[start, end)``; the last week by default."""

        last = end or self.today()
        first = start or (last - timedelta(days=DEFAULT_DAYS - 1))
        if first > last:
            raise InvalidInputError("La fecha inicial es posterior a la final")
        return self._day_start(first), self._day_start(last + timedelta(days=1))

    @staticmethod
    def _check_page(page: int, size: int) -> None:
        if page < 1 or not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"Página desde 1 y tamaño entre 1 y {MAX_PAGE_SIZE}")

    # --- Load status ---

    async def sync_status(self, principal: Principal) -> dict[str, Any]:
        """Whether the background load is on and how the last cycle ended.

        Args:
            principal (Principal): The caller.

        Returns:
            dict[str, Any]: ``habilitada``, ``intervalo_s``, ``ultima`` and
            ``ultima_exitosa``.
        """

        await self._authorize(principal)
        return await self._sync_status()

    async def _sync_status(self) -> dict[str, Any]:
        return {
            "habilitada": self.sync_enabled,
            "intervalo_s": self.sync_interval,
            "ultima": await self.repository.last_cycle(),
            "ultima_exitosa": await self.repository.last_successful_load(),
        }

    async def request_sync(self, principal: Principal) -> None:
        """Ask the background load to run now.

        Args:
            principal (Principal): The caller.

        Raises:
            ConflictError: If the background load is off in this process.
        """

        await self._authorize(principal)
        if not self.sync_enabled or self.request_sync_now is None:
            raise ConflictError("La carga automática del monitoreo está apagada")
        self.request_sync_now()

    # --- Screens ---

    async def overview(self, principal: Principal) -> dict[str, Any]:
        """Header of the module: freshness KPI, sequences and today's ready times.

        Args:
            principal (Principal): The caller.

        Returns:
            dict[str, Any]: The overview.
        """

        await self._authorize(principal)
        sequences = await self.repository.sequences()
        start = self._day_start(self.today())
        ready_today = [
            {
                "secuencia": row["secuencia"],
                "ultima_estado": row["ultima_estado"],
                "datos_listos": (
                    row["ultima_datos_listos"]
                    if row["ultima_datos_listos"] and row["ultima_datos_listos"] >= start
                    else None
                ),
                "eta_datos_listos": row["eta_datos_listos_en_curso"]
                or row["eta_datos_listos_proxima"],
                "hora_tipica_datos_listos": row["hora_tipica_datos_listos"],
                "corridas_medidas": row["corridas_medidas"],
            }
            for row in sequences
            if row["estado_programacion"] == "Activa"
        ]
        return {
            "zona_horaria": self.timezone,
            "frescura": await self.repository.freshness_latest(),
            "secuencias_activas": sum(
                1 for row in sequences if row["estado_programacion"] == "Activa"
            ),
            "secuencias_en_curso": sum(1 for row in sequences if row["en_curso"]),
            "datos_listos_hoy": ready_today,
            "carga": await self._sync_status(),
        }

    async def sequences(self, principal: Principal) -> list[Row]:
        """Every sequence card."""

        await self._authorize(principal)
        return await self.repository.sequences()

    async def sequence(self, name: str, principal: Principal) -> dict[str, Any]:
        """One sequence with its tables, typical schedule and statistics.

        Raises:
            NotFoundError: If the sequence is not in the copy.
        """

        await self._authorize(principal)
        row = await self.repository.sequence(name)
        if row is None:
            raise NotFoundError("Secuencia no encontrada")
        statistics = await self.repository.statistics(name)
        return {
            **row,
            "tablas": await self.repository.sequence_tables(name, None, None),
            "horario": await self.repository.typical_schedule(name),
            "estadisticas": statistics[0] if statistics else None,
        }

    async def sequence_tables(
        self,
        principal: Principal,
        secuencia: str | None = None,
        dominio: str | None = None,
        fase: int | None = None,
    ) -> list[Row]:
        """Tables each sequence extracts."""

        await self._authorize(principal)
        return await self.repository.sequence_tables(secuencia, dominio, fase)

    async def statistics(self, principal: Principal) -> list[Row]:
        """Medians and p90 behind the ETAs, per sequence."""

        await self._authorize(principal)
        return await self.repository.statistics()

    async def freshness(self, principal: Principal) -> dict[str, Any]:
        """Freshness KPI and its breakdown by domain."""

        await self._authorize(principal)
        return {
            "resumen": await self.repository.freshness_latest(),
            "dominios": await self.repository.freshness_domains(),
        }

    async def freshness_tables(
        self,
        principal: Principal,
        estados: Sequence[str] = (),
        dominio: str | None = None,
        programacion: str | None = None,
        query: str | None = None,
    ) -> list[Row]:
        """Freshness per table (the late ones: ``Atrasada`` and ``Nunca extraída``)."""

        await self._authorize(principal)
        return await self.repository.freshness_tables(
            estados, dominio, programacion, (query or "").strip() or None
        )

    async def freshness_history(
        self, principal: Principal, start: date | None = None, end: date | None = None
    ) -> list[Row]:
        """Freshness KPI over time, one point per load."""

        await self._authorize(principal)
        return await self.repository.freshness_history(*self._range(start, end))

    async def schedule(self, principal: Principal, day: date | None = None) -> dict[str, Any]:
        """Typical day against the runs of one day, plus the ETAs of each sequence.

        Args:
            principal (Principal): The caller.
            day (date | None): Business day to compare; today by default.

        Returns:
            dict[str, Any]: ``fecha``, ``tipico``, ``corridas`` and ``secuencias``.
        """

        await self._authorize(principal)
        day = day or self.today()
        start = self._day_start(day)
        sequences = await self.repository.sequences()
        return {
            "fecha": day,
            "zona_horaria": self.timezone,
            "tipico": await self.repository.typical_schedule(),
            "corridas": await self.repository.runs_between(start, start + timedelta(days=1)),
            "secuencias": sequences,
        }

    async def runs(
        self,
        principal: Principal,
        secuencia: str | None = None,
        estados: Sequence[str] = (),
        start: date | None = None,
        end: date | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[Row], int]:
        """Page the sequence run history (all dates unless a range is given)."""

        await self._authorize(principal)
        self._check_page(page, size)
        first, last = self._range(start, end) if start or end else (None, None)
        filters = RunFilters(start=first, end=last, secuencia=secuencia, estados=estados)
        return await self.repository.runs(filters, page, size)

    async def extractions(
        self,
        principal: Principal,
        start: date | None = None,
        end: date | None = None,
        secuencia: str | None = None,
        dominio: str | None = None,
        estados: Sequence[str] = (),
        disparo: str | None = None,
        query: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[Row], int]:
        """Page the extraction audit; always within a date range (last week by default)."""

        await self._authorize(principal)
        self._check_page(page, size)
        first, last = self._range(start, end)
        filters = RunFilters(
            start=first,
            end=last,
            secuencia=secuencia,
            dominio=dominio,
            estados=estados,
            disparo=disparo,
            query=(query or "").strip() or None,
        )
        return await self.repository.extractions(filters, page, size)

    async def transformations(
        self,
        principal: Principal,
        start: date | None = None,
        end: date | None = None,
        secuencia: str | None = None,
        estados: Sequence[str] = (),
        query: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[Row], int]:
        """Page the transformation audit; always within a date range."""

        await self._authorize(principal)
        self._check_page(page, size)
        first, last = self._range(start, end)
        filters = RunFilters(
            start=first,
            end=last,
            secuencia=secuencia,
            estados=estados,
            query=(query or "").strip() or None,
        )
        return await self.repository.transformations(filters, page, size)

    async def quality(
        self, principal: Principal, estados: Sequence[str] = (), tipo: str | None = None
    ) -> list[Row]:
        """Latest result of each test and model."""

        await self._authorize(principal)
        return await self.repository.quality(estados, tipo)
