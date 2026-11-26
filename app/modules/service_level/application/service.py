"""Nivel de servicio: the pending lines of the fulfilled orders, their reasons, the dashboard, the
ABCD classes and the daily job that copies the lines from the warehouse."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.modules.identity.domain.principal import Principal
from app.modules.service_level.application.access import ModuleAccess, ServiceLevelAccess
from app.modules.service_level.domain.entities import (
    CLASS_KINDS,
    CLASSES,
    DIMENSIONS,
    GROUP_SORTS,
    LINE_SORTS,
    MAX_NOTE,
    MAX_REASON_NAME,
    MAX_RESPONSIBLE,
    PRODUCT,
    Bands,
    ClassRun,
    Cuts,
    Group,
    Kpis,
    LineFilters,
    LineRow,
    ParetoPoint,
    Reason,
    SyncReport,
    class_counts,
    class_window,
    classify,
    kpis,
    month_chunks,
    month_start,
    pareto,
)
from app.modules.service_level.domain.modules import PENDING_ORDERS
from app.modules.service_level.domain.ports import (
    FilterChoices,
    OrderLineSource,
    ServiceLevelRepository,
)

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 200
MAX_LINES_AT_ONCE = 500
_BOGOTA = ZoneInfo("America/Bogota")


@dataclass(frozen=True)
class LinesPage:
    items: list[LineRow]
    total: int
    page: int
    size: int
    kpis: Kpis
    since: date
    until: date
    as_of: datetime | None


@dataclass(frozen=True)
class GroupKpis:
    group: Group
    kpis: Kpis


@dataclass(frozen=True)
class Dashboard:
    since: date
    until: date
    kpis: Kpis
    by_co: list[GroupKpis]
    daily: list[tuple[date, Kpis]]
    by_responsible: list[Group]
    as_of: datetime | None
    classes: ClassRun | None


@dataclass(frozen=True)
class GroupPage:
    dimension: str
    items: list[Group]
    total: int
    page: int
    size: int


@dataclass(frozen=True)
class Pareto:
    kind: str
    co: str | None
    points: list[ParetoPoint]
    items: int
    run: ClassRun | None


def reason_code(name: str) -> str:
    """``"AGOTADO PROVEEDOR"`` → ``"agotado-proveedor"``."""

    words = "".join(c.lower() if c.isalnum() else " " for c in name).split()
    return "-".join(words)[:80]


def _clean_name(value: str | None, label: str, limit: int) -> str:
    text = " ".join((value or "").split()).upper()
    if not text or len(text) > limit:
        raise InvalidInputError(f"{label} debe tener entre 1 y {limit} caracteres")
    return text


class ServiceLevelService:
    """Pendientes de pedidos (``pendientes-pedidos``) and Nivel de servicio (``nivel-servicio``)."""

    def __init__(
        self,
        repository: ServiceLevelRepository,
        source: OrderLineSource,
        access: ServiceLevelAccess,
        cuts: Cuts,
        bands: Bands,
        class_months: int,
        sync_days: int,
        start_date: date,
        max_range_days: int,
        today: Callable[[], date] = lambda: datetime.now(_BOGOTA).date(),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create the service.

        Args:
            repository (ServiceLevelRepository): The copied lines, reasons and classes.
            source (OrderLineSource): The warehouse's order lines.
            access (ServiceLevelAccess): Who reads and manages each key.
            cuts (Cuts): The running shares that close classes A, B and C.
            bands (Bands): The colours of a service level.
            class_months (int): Closed months the classes read.
            sync_days (int): Days the job re-reads (the warehouse re-reads 45).
            start_date (date): The job never copies orders older than this.
            max_range_days (int): Longest period a query reads.
            today (Callable[[], date]): Today in Bogotá.
            clock (Callable[[], datetime]): Now, in UTC.
        """

        self.repository = repository
        self.source = source
        self.access = access
        self.cuts = cuts
        self.bands = bands
        self.class_months = class_months
        self.sync_days = sync_days
        self.start_date = start_date
        self.max_range_days = max_range_days
        self.today = today
        self.clock = clock

    async def permissions(self, principal: Principal) -> dict[str, ModuleAccess]:
        return await self.access.permissions(principal)

    # --- filters ---

    def period(self, since: date | None, until: date | None) -> tuple[date, date]:
        """The days a query reads: the current month by default.

        Raises:
            InvalidInputError: The period ends before it starts, or is too long.
        """

        today = self.today()
        until = until or (today if since is None or since <= today else since)
        since = since or month_start(until)
        if since > until:
            raise InvalidInputError("La fecha inicial es posterior a la final")
        if (until - since).days + 1 > self.max_range_days:
            raise InvalidInputError(f"El periodo no puede pasar de {self.max_range_days} días")
        return since, until

    def _check(self, filters: LineFilters) -> None:
        self.period(filters.since, filters.until)
        for value in (filters.product_class, filters.customer_class):
            if value is not None and value not in (*CLASSES, "none"):
                raise InvalidInputError("Clase ABCD desconocida")

    # --- reasons ---

    async def reasons(self, include_inactive: bool = False) -> list[Reason]:
        return await self.repository.reasons(include_inactive)

    async def responsibles(self) -> list[str]:
        return await self.repository.responsibles()

    async def create_reason(self, principal: Principal, name: str, responsible: str) -> Reason:
        """Add a reason to the catalog.

        Raises:
            ForbiddenError: The caller does not manage the pending lines.
            InvalidInputError: Empty or too long name or responsible.
            ConflictError: A reason with that name exists.
        """

        await self.access.require_manage(
            principal, PENDING_ORDERS, "No tienes permiso para cambiar los motivos"
        )
        name = _clean_name(name, "El nombre del motivo", MAX_REASON_NAME)
        responsible = _clean_name(responsible, "El responsable", MAX_RESPONSIBLE)
        code = reason_code(name)
        if not code:
            raise InvalidInputError("El nombre del motivo necesita letras o números")
        existing = await self.repository.reasons(include_inactive=True)
        if any(reason.code == code for reason in existing):
            raise ConflictError("Ya existe un motivo con ese nombre")
        return await self.repository.create_reason(code, name, responsible)

    async def update_reason(
        self,
        principal: Principal,
        reason_id: UUID,
        *,
        name: str | None = None,
        responsible: str | None = None,
        is_active: bool | None = None,
    ) -> Reason:
        """Rename a reason, change who answers for it, or switch it off (lines keep it).

        Raises:
            ForbiddenError: The caller does not manage the pending lines.
            InvalidInputError: Empty or too long name or responsible.
            NotFoundError: No such reason.
            ConflictError: Another reason has that name.
        """

        await self.access.require_manage(
            principal, PENDING_ORDERS, "No tienes permiso para cambiar los motivos"
        )
        if await self.repository.get_reason(reason_id) is None:
            raise NotFoundError("Motivo no encontrado")
        if name is not None:
            name = _clean_name(name, "El nombre del motivo", MAX_REASON_NAME)
            code = reason_code(name)
            existing = await self.repository.reasons(include_inactive=True)
            if any(reason.code == code and reason.id != reason_id for reason in existing):
                raise ConflictError("Ya existe un motivo con ese nombre")
        if responsible is not None:
            responsible = _clean_name(responsible, "El responsable", MAX_RESPONSIBLE)
        return await self.repository.update_reason(
            reason_id, name=name, responsible=responsible, is_active=is_active
        )

    # --- the lines ---

    async def lines(
        self, filters: LineFilters, sort: str, descending: bool, page: int, size: int
    ) -> LinesPage:
        """A page of the lines with the KPIs of the whole filter.

        Raises:
            InvalidInputError: Bad period, class, sort or page.
        """

        self._check(filters)
        if page < 1 or not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"Página inválida; el tamaño máximo es {MAX_PAGE_SIZE}")
        if sort not in LINE_SORTS:
            raise InvalidInputError("Orden desconocido")
        items, total = await self.repository.page(filters, sort, descending, page, size)
        totals = await self.repository.totals(filters)
        return LinesPage(
            items,
            total,
            page,
            size,
            kpis(totals),
            filters.since,
            filters.until,
            await self.repository.data_as_of(),
        )

    async def choices(self, since: date, until: date) -> FilterChoices:
        self.period(since, until)
        return await self.repository.choices(since, until)

    async def set_reason(
        self,
        principal: Principal,
        line_keys: Sequence[str],
        reason_id: UUID | None,
        note: str | None,
    ) -> list[LineRow]:
        """Give the lines a reason (``None`` takes it away), with an optional note.

        Only lines with something pending take a reason.

        Raises:
            ForbiddenError: The caller does not manage the pending lines.
            InvalidInputError: No lines, too many, a line served in full, an inactive reason or
                a note too long.
            NotFoundError: No such line or reason.
        """

        await self.access.require_manage(
            principal, PENDING_ORDERS, "No tienes permiso para asignar motivos"
        )
        keys = list(dict.fromkeys(key.strip() for key in line_keys if key and key.strip()))
        if not keys:
            raise InvalidInputError("Elige al menos una línea")
        if len(keys) > MAX_LINES_AT_ONCE:
            raise InvalidInputError(f"Se pueden asignar hasta {MAX_LINES_AT_ONCE} líneas a la vez")
        note = " ".join((note or "").split()) or None
        if note is not None and len(note) > MAX_NOTE:
            raise InvalidInputError(f"La observación pasa de {MAX_NOTE} caracteres")
        if reason_id is not None:
            reason = await self.repository.get_reason(reason_id)
            if reason is None:
                raise NotFoundError("Motivo no encontrado")
            if not reason.is_active:
                raise InvalidInputError("El motivo está inactivo")
        found = await self.repository.lines_by_key(keys)
        missing = [key for key in keys if key not in found]
        if missing:
            raise NotFoundError(f"{len(missing)} línea(s) no existen")
        served = [key for key in keys if not found[key].line.is_pending]
        if served and reason_id is not None:
            raise InvalidInputError(
                f"{len(served)} línea(s) no tienen pendiente: solo las pendientes llevan motivo"
            )
        await self.repository.set_reasons(keys, reason_id, note, principal.email, self.clock())
        updated = await self.repository.lines_by_key(keys)
        return [updated[key] for key in keys]

    # --- the dashboard ---

    async def dashboard(self, filters: LineFilters) -> Dashboard:
        """KPIs of the filter, by C.O. and by day, and the pending value by responsible.

        Raises:
            InvalidInputError: Bad period or class.
        """

        self._check(filters)
        totals = await self.repository.totals(filters)
        by_co, _ = await self.repository.groups(filters, "co", "key", False, None, 0)
        daily = await self.repository.daily(filters)
        responsible, _ = await self.repository.groups(
            filters, "responsible", "value_pending", True, None, 0
        )
        return Dashboard(
            since=filters.since,
            until=filters.until,
            kpis=kpis(totals),
            by_co=[GroupKpis(group, kpis(group.totals)) for group in by_co],
            daily=[(day, kpis(day_totals)) for day, day_totals in daily],
            by_responsible=responsible,
            as_of=await self.repository.data_as_of(),
            classes=await self.repository.latest_class_run(),
        )

    async def breakdown(
        self,
        filters: LineFilters,
        dimension: str,
        sort: str,
        descending: bool,
        page: int,
        size: int,
    ) -> GroupPage:
        """The lines grouped by one key (supplier, seller, customer, reason …).

        Raises:
            InvalidInputError: Bad period, class, grouping, sort or page.
        """

        self._check(filters)
        if dimension not in DIMENSIONS:
            raise InvalidInputError("Agrupación desconocida")
        if sort not in GROUP_SORTS:
            raise InvalidInputError("Orden desconocido")
        if page < 1 or not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"Página inválida; el tamaño máximo es {MAX_PAGE_SIZE}")
        items, total = await self.repository.groups(
            filters, dimension, sort, descending, size, (page - 1) * size
        )
        return GroupPage(dimension, items, total, page, size)

    # --- ABCD ---

    def class_window(self) -> tuple[date, date]:
        return class_window(self.today(), self.class_months)

    async def class_run(self) -> ClassRun | None:
        return await self.repository.latest_class_run()

    async def pareto(self, kind: str, co: str | None) -> Pareto:
        """The Pareto curve of the stored classes: products (of a C.O. or all) or customers.

        Raises:
            InvalidInputError: Unknown kind.
        """

        if kind not in CLASS_KINDS:
            raise InvalidInputError("Tipo de clasificación desconocido")
        values = await self.repository.stored_class_values(kind, co if kind == PRODUCT else None)
        return Pareto(
            kind,
            co if kind == PRODUCT else None,
            pareto(values),
            len(values),
            await self.repository.latest_class_run(),
        )

    async def recompute_classes(self, principal: Principal) -> ClassRun:
        """Classify again now with the last closed months.

        Raises:
            ForbiddenError: The caller does not manage the pending lines.
        """

        await self.access.require_manage(
            principal, PENDING_ORDERS, "No tienes permiso para recalcular la clasificación"
        )
        return await self._classify(principal.email)

    async def _classify(self, actor: str | None) -> ClassRun:
        since, until = self.class_window()
        classes = {}
        for kind in CLASS_KINDS:
            values = await self.repository.class_values(kind, since, until)
            classes[kind] = classify(values, self.cuts)
        run = ClassRun(
            id=uuid4(),
            period_from=since,
            period_to=until,
            computed_at=self.clock(),
            computed_by=actor,
            counts={kind: class_counts(items) for kind, items in classes.items()},
        )
        await self.repository.replace_classes(classes, run)
        return run

    # --- the daily job ---

    async def sync(self) -> Mapping[str, Any]:
        """Copy the lines of the orders fulfilled in the last days and classify when the month
        changed.

        - The first run copies from the start date, a month at a time.
        - The days' lines are refreshed; one no longer in the warehouse goes away, or stays
          voided (out of every figure) when it has a reason.
        - An empty read keeps the copy: a warehouse being rebuilt is not every order gone.
        - Until the warehouse publishes the order lines, the job does nothing.

        Returns:
            Mapping[str, Any]: What the run did (:class:`SyncReport`).
        """

        if not await self.source.available():
            logger.warning("service-level-sync: the warehouse has no order lines view yet")
            return SyncReport(
                None, None, skipped="El DWH aún no publica las líneas de pedido"
            ).as_dict()
        today = self.today()
        since = self.start_date
        if await self.repository.has_lines():
            since = max(self.start_date, today - timedelta(days=self.sync_days - 1))
        read = upserted = removed = voided = 0
        for start, end in month_chunks(since, today):
            lines = await self.source.lines(start, end)
            read += len(lines)
            result = await self.repository.store_lines(start, end, lines, self.clock())
            upserted += result.upserted
            removed += result.removed
            voided += result.voided
        classes = None
        run = await self.repository.latest_class_run()
        window = self.class_window()
        if run is None or (run.period_from, run.period_to) != window:
            classes = (await self._classify(None)).counts
        return SyncReport(since, today, read, upserted, removed, voided, classes=classes).as_dict()


__all__ = [
    "MAX_LINES_AT_ONCE",
    "MAX_PAGE_SIZE",
    "Dashboard",
    "GroupKpis",
    "GroupPage",
    "LinesPage",
    "Pareto",
    "ServiceLevelService",
    "reason_code",
]
