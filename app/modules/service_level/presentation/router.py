"""Endpoints of Nivel de servicio (Abastecimiento): the pending lines of the fulfilled orders, the
reasons, the dashboard and the ABCD classes."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.core.errors import InvalidInputError
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.service_level.application.service import (
    MAX_LINES_AT_ONCE,
    MAX_PAGE_SIZE,
    ServiceLevelService,
)
from app.modules.service_level.domain.entities import (
    WITHOUT_REASON,
    ClassRun,
    Group,
    Kpis,
    LineFilters,
    LineRow,
    Reason,
)
from app.modules.service_level.domain.modules import PENDING_ORDERS, SERVICE_LEVEL

router = APIRouter()
_PENDING_GATE = Depends(require_permission(PENDING_ORDERS))
_EITHER_GATE = Depends(require_permission(PENDING_ORDERS, SERVICE_LEVEL))
_service = Depends(Provide[ApplicationContainer.v1_container.service_level_service])
_principal = Depends(get_current_principal)

LineSort = Literal[
    "date",
    "document",
    "co",
    "sku",
    "supplier",
    "customer",
    "seller",
    "qty_pending",
    "value_pending",
    "value",
]
GroupSort = Literal["value", "value_pending", "qty_pending", "ns_value", "name", "key"]
Dimension = Literal[
    "co",
    "warehouse",
    "supplier",
    "seller",
    "customer",
    "responsible",
    "reason",
    "product",
    "reason_item",
    "product_class",
    "customer_class",
]
ClassFilter = Literal["A", "B", "C", "D", "none"]
ClassKind = Literal["producto", "cliente"]
Order = Literal["asc", "desc"]

_SINCE = Query(default=None, description="First order day (YYYY-MM-DD)")
_UNTIL = Query(default=None, description="Last order day (YYYY-MM-DD)")
_CO_FILTER = Query(default=None, max_length=20, description="Operation center code")
_WAREHOUSE = Query(default=None, max_length=40, description="Warehouse code")
_SUPPLIER = Query(default=None, max_length=40, description="Supplier code")
_SELLER = Query(default=None, max_length=64, description="Seller key")
_CUSTOMER = Query(default=None, max_length=40, description="Customer NIT")
_SEARCH = Query(default=None, max_length=100, description="Reference, description or order")
_REASON = Query(default=None, description="Reason id, or «none» for the lines without one")
_RESPONSIBLE = Query(default=None, max_length=60)
# One Query per parameter: a Query shared by parameters of different types lost ``pending``.
_PRODUCT_CLASS = Query(default=None, description="ABCD class of the product in its C.O.")
_CUSTOMER_CLASS = Query(default=None, description="ABCD class of the customer's NIT")
_PENDING = Query(default=None, description="true: only lines with something pending")
_ORDER = Query(default="desc")
_PAGE = Query(default=1, ge=1)
_SIZE = Query(default=50, ge=1, le=MAX_PAGE_SIZE)
_LINE_SORT = Query(default="value_pending")
_GROUP_SORT = Query(default="value")
_DIMENSION = Query()
_KIND = Query(default="producto")
_CO = Query(default=None, max_length=20)
_INACTIVE = Query(default=False)


# --- Schemas ---


class ModuleAccessResponse(BaseModel):
    can_read: bool
    can_manage: bool


class PermissionsResponse(BaseModel):
    """What the caller may do in each view, by permission key."""

    modules: dict[str, ModuleAccessResponse]
    # Give reasons to lines, keep the reasons' catalog and recalculate the ABCD.
    can_manage: bool


class ReasonResponse(BaseModel):
    id: UUID
    code: str
    name: str
    responsible: str
    is_active: bool
    sort_order: int


class ReasonCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    responsible: str = Field(min_length=1, max_length=60)


class ReasonPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    responsible: str | None = Field(default=None, max_length=60)
    is_active: bool | None = None


class BandsResponse(BaseModel):
    """A service level is green above ``green_above``, amber from ``amber_from``, else red."""

    green_above: Decimal
    amber_from: Decimal


class KpisResponse(BaseModel):
    """Sums and service levels (fractions: 0.995 is 99.5 %; ``null`` when nothing was asked).

    ``ns_total`` = (``ns_value`` + ``ns_qty`` + ``ns_lines``) × 0.3 + ``ns_orders`` × 0.1.
    """

    orders: int
    orders_pending: int
    lines: int
    lines_pending: int
    qty: Decimal
    qty_pending: Decimal
    value: Decimal
    value_served: Decimal
    value_pending: Decimal
    ns_orders: Decimal | None
    ns_lines: Decimal | None
    ns_qty: Decimal | None
    ns_value: Decimal | None
    ns_total: Decimal | None


class ReasonBrief(BaseModel):
    id: UUID
    name: str
    responsible: str


class LineResponse(BaseModel):
    """A line of a fulfilled order: what was asked, dispatched and left pending."""

    line_key: str
    order_key: str
    document: str | None
    co: str | None
    co_name: str | None
    order_date: date
    # The order's last change in SIESA.
    updated_at: datetime | None
    warehouse_code: str | None
    warehouse_name: str | None
    sku: str
    description: str | None
    supplier_code: str | None
    supplier_name: str | None
    customer_key: str | None
    customer_nit: str | None
    customer_name: str | None
    customer_branch: str | None
    seller_key: str | None
    seller_name: str | None
    qty_ordered: Decimal
    qty_dispatched: Decimal
    qty_pending: Decimal
    value_ordered: Decimal
    value_pending: Decimal
    is_pending: bool
    reason: ReasonBrief | None
    note: str | None
    reason_by: str | None
    reason_at: datetime | None
    product_class: str | None
    customer_class: str | None


class LinesPageResponse(BaseModel):
    items: list[LineResponse]
    total: int
    page: int
    size: int
    since: date
    until: date
    # The KPIs of every line of the filter, not only the page.
    kpis: KpisResponse
    bands: BandsResponse
    # When the job last copied the lines from the warehouse.
    as_of: datetime | None


class ReasonChoice(BaseModel):
    """The reason of a line; ``null`` takes it away."""

    reason_id: UUID | None
    note: str | None = Field(default=None, max_length=500)


class ReasonAssignment(ReasonChoice):
    line_keys: list[str] = Field(min_length=1, max_length=MAX_LINES_AT_ONCE)


class AssignedResponse(BaseModel):
    updated: int
    items: list[LineResponse]


class GroupResponse(BaseModel):
    """The lines that share a key. ``ns_value`` = served / asked; ``pending_pct`` = 1 − it."""

    key: str | None
    name: str | None
    # The product of a reason × product row.
    detail_key: str | None = None
    detail_name: str | None = None
    orders: int
    orders_pending: int
    lines: int
    lines_pending: int
    qty: Decimal
    qty_pending: Decimal
    value: Decimal
    value_served: Decimal
    value_pending: Decimal
    ns_value: Decimal | None
    pending_pct: Decimal | None


class CoKpisResponse(BaseModel):
    co: str | None
    name: str | None
    kpis: KpisResponse


class DayKpisResponse(BaseModel):
    day: date
    kpis: KpisResponse


class ClassRunResponse(BaseModel):
    id: UUID
    period_from: date
    period_to: date
    computed_at: datetime
    # ``null``: the daily job.
    computed_by: str | None
    # Kind («producto», «cliente») → class → items.
    counts: dict[str, dict[str, int]]


class DashboardResponse(BaseModel):
    since: date
    until: date
    kpis: KpisResponse
    bands: BandsResponse
    by_co: list[CoKpisResponse]
    daily: list[DayKpisResponse]
    # The pending value by responsible (the doughnut); lines with nothing pending are out.
    by_responsible: list[GroupResponse]
    as_of: datetime | None
    classes: ClassRunResponse | None


class BreakdownResponse(BaseModel):
    dimension: str
    items: list[GroupResponse]
    total: int
    page: int
    size: int


class ParetoPointResponse(BaseModel):
    items: Decimal
    value: Decimal


class ParetoResponse(BaseModel):
    """The Pareto curve of the requested value: ``items`` of the items ask for ``value``."""

    kind: str
    co: str | None
    items: int
    points: list[ParetoPointResponse]
    run: ClassRunResponse | None


class CutsResponse(BaseModel):
    a: Decimal
    b: Decimal
    c: Decimal


class ClassStatusResponse(BaseModel):
    # The closed months the next calculation reads.
    window_from: date
    window_to: date
    cuts: CutsResponse
    run: ClassRunResponse | None


class Choice(BaseModel):
    code: str
    name: str


class OptionsResponse(BaseModel):
    cos: list[Choice]
    warehouses: list[Choice]
    suppliers: list[Choice]
    sellers: list[Choice]
    responsibles: list[str]


# --- Mapping ---


def _kpis(item: Kpis) -> KpisResponse:
    totals = item.totals
    return KpisResponse(
        orders=totals.orders,
        orders_pending=totals.orders_pending,
        lines=totals.lines,
        lines_pending=totals.lines_pending,
        qty=totals.qty,
        qty_pending=totals.qty_pending,
        value=totals.value,
        value_served=totals.value_served,
        value_pending=totals.value_pending,
        ns_orders=item.ns_orders,
        ns_lines=item.ns_lines,
        ns_qty=item.ns_qty,
        ns_value=item.ns_value,
        ns_total=item.ns_total,
    )


def _reason(item: Reason) -> ReasonResponse:
    return ReasonResponse(
        id=item.id,
        code=item.code,
        name=item.name,
        responsible=item.responsible,
        is_active=item.is_active,
        sort_order=item.sort_order,
    )


def _line(row: LineRow) -> LineResponse:
    line = row.line
    return LineResponse(
        line_key=line.line_key,
        order_key=line.order_key,
        document=line.document,
        co=line.co,
        co_name=line.co_name,
        order_date=line.order_date,
        updated_at=line.updated_at,
        warehouse_code=line.warehouse_code,
        warehouse_name=line.warehouse_name,
        sku=line.sku,
        description=line.description,
        supplier_code=line.supplier_code,
        supplier_name=line.supplier_name,
        customer_key=line.customer_key,
        customer_nit=line.customer_nit,
        customer_name=line.customer_name,
        customer_branch=line.customer_branch,
        seller_key=line.seller_key,
        seller_name=line.seller_name,
        qty_ordered=line.qty_ordered,
        qty_dispatched=line.qty_dispatched,
        qty_pending=line.qty_pending,
        value_ordered=line.value_ordered,
        value_pending=line.value_pending,
        is_pending=line.is_pending,
        reason=None
        if row.reason is None
        else ReasonBrief(
            id=row.reason.id, name=row.reason.name, responsible=row.reason.responsible
        ),
        note=row.note,
        reason_by=row.reason_by,
        reason_at=row.reason_at,
        product_class=row.product_class,
        customer_class=row.customer_class,
    )


def _group(item: Group) -> GroupResponse:
    totals = item.totals
    ns_value = None if totals.value <= 0 else 1 - totals.value_pending / totals.value
    return GroupResponse(
        key=item.key,
        name=item.name,
        detail_key=item.detail_key,
        detail_name=item.detail_name,
        orders=totals.orders,
        orders_pending=totals.orders_pending,
        lines=totals.lines,
        lines_pending=totals.lines_pending,
        qty=totals.qty,
        qty_pending=totals.qty_pending,
        value=totals.value,
        value_served=totals.value_served,
        value_pending=totals.value_pending,
        ns_value=ns_value,
        pending_pct=None if ns_value is None else 1 - ns_value,
    )


def _run(item: ClassRun | None) -> ClassRunResponse | None:
    if item is None:
        return None
    return ClassRunResponse(
        id=item.id,
        period_from=item.period_from,
        period_to=item.period_to,
        computed_at=item.computed_at,
        computed_by=item.computed_by,
        counts=item.counts,
    )


def _bands(service: ServiceLevelService) -> BandsResponse:
    return BandsResponse(green_above=service.bands.green_above, amber_from=service.bands.amber_from)


class FilterParams:
    """The filters every view of the module takes, from the query string."""

    def __init__(
        self,
        since: date | None = _SINCE,
        until: date | None = _UNTIL,
        co: str | None = _CO_FILTER,
        warehouse: str | None = _WAREHOUSE,
        supplier: str | None = _SUPPLIER,
        seller: str | None = _SELLER,
        customer: str | None = _CUSTOMER,
        q: str | None = _SEARCH,
        reason: str | None = _REASON,
        responsible: str | None = _RESPONSIBLE,
        product_class: ClassFilter | None = _PRODUCT_CLASS,
        customer_class: ClassFilter | None = _CUSTOMER_CLASS,
        pending: bool | None = _PENDING,
    ) -> None:
        self.since = since
        self.until = until
        self.co = co
        self.warehouse = warehouse
        self.supplier = supplier
        self.seller = seller
        self.customer = customer
        self.q = q
        self.reason = reason
        self.responsible = responsible
        self.product_class = product_class
        self.customer_class = customer_class
        self.pending = pending

    def build(self, service: ServiceLevelService, pending: bool | None = None) -> LineFilters:
        """The filters, with the period checked (the current month by default).

        Raises:
            InvalidInputError: Bad period or reason id.
        """

        since, until = service.period(self.since, self.until)
        reason_id = None
        without_reason = False
        if self.reason == WITHOUT_REASON:
            without_reason = True
        elif self.reason:
            try:
                reason_id = UUID(self.reason)
            except ValueError as exc:
                raise InvalidInputError("Motivo inválido") from exc
        return LineFilters(
            since=since,
            until=until,
            co=self.co or None,
            warehouse=self.warehouse or None,
            supplier=self.supplier or None,
            seller=self.seller or None,
            customer=self.customer or None,
            q=self.q or None,
            reason_id=reason_id,
            without_reason=without_reason,
            responsible=(self.responsible or "").strip().upper() or None,
            product_class=self.product_class,
            customer_class=self.customer_class,
            pending=self.pending if pending is None else pending,
        )


_filters = Depends(FilterParams)


# --- Endpoints ---


@router.get("/permissions", response_model=PermissionsResponse)
@inject
async def permissions(
    principal: Principal = _principal, service: ServiceLevelService = _service
) -> PermissionsResponse:
    """What the caller may do in each view (open: a caller with nothing gets ``false``)."""

    access = await service.permissions(principal)
    return PermissionsResponse(
        modules={
            key: ModuleAccessResponse(can_read=item.can_read, can_manage=item.can_manage)
            for key, item in access.items()
        },
        can_manage=access[PENDING_ORDERS].can_manage,
    )


@router.get("/reasons", response_model=list[ReasonResponse], dependencies=[_EITHER_GATE])
@inject
async def list_reasons(
    include_inactive: bool = _INACTIVE, service: ServiceLevelService = _service
) -> list[ReasonResponse]:
    """The reasons of the catalog, in their order (the active ones unless asked)."""

    return [_reason(item) for item in await service.reasons(include_inactive)]


@router.get("/reasons/responsibles", response_model=list[str], dependencies=[_EITHER_GATE])
@inject
async def list_responsibles(service: ServiceLevelService = _service) -> list[str]:
    """Who answers for the active reasons (PROVEEDOR, COMERCIAL, LOGISTICA …)."""

    return await service.responsibles()


@router.post(
    "/reasons",
    response_model=ReasonResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_PENDING_GATE],
)
@inject
async def create_reason(
    body: ReasonCreate, principal: Principal = _principal, service: ServiceLevelService = _service
) -> ReasonResponse:
    """Add a reason with who answers for it (``pendientes-pedidos: manage``)."""

    return _reason(await service.create_reason(principal, body.name, body.responsible))


@router.patch("/reasons/{reason_id}", response_model=ReasonResponse, dependencies=[_PENDING_GATE])
@inject
async def update_reason(
    reason_id: UUID,
    body: ReasonPatch,
    principal: Principal = _principal,
    service: ServiceLevelService = _service,
) -> ReasonResponse:
    """Rename a reason, change its responsible or switch it off; the lines keep it."""

    return _reason(
        await service.update_reason(
            principal,
            reason_id,
            name=body.name,
            responsible=body.responsible,
            is_active=body.is_active,
        )
    )


@router.get("/options", response_model=OptionsResponse, dependencies=[_EITHER_GATE])
@inject
async def options(
    filters: FilterParams = _filters, service: ServiceLevelService = _service
) -> OptionsResponse:
    """The C.O., warehouses, suppliers and sellers of the period's lines (the dropdowns)."""

    period = filters.build(service)
    choices = await service.choices(period.since, period.until)

    def listed(values) -> list[Choice]:
        return [Choice(code=code, name=name) for code, name in values.items()]

    return OptionsResponse(
        cos=listed(choices.cos),
        warehouses=listed(choices.warehouses),
        suppliers=listed(choices.suppliers),
        sellers=listed(choices.sellers),
        responsibles=await service.responsibles(),
    )


@router.get("/lines", response_model=LinesPageResponse, dependencies=[_PENDING_GATE])
@inject
async def list_lines(
    filters: FilterParams = _filters,
    sort: LineSort = _LINE_SORT,
    order: Order = _ORDER,
    page: int = _PAGE,
    size: int = _SIZE,
    service: ServiceLevelService = _service,
) -> LinesPageResponse:
    """The lines of the fulfilled orders of the period (the current month by default) with the
    KPIs of the whole filter. ``pending=true`` keeps the lines with something pending."""

    result = await service.lines(filters.build(service), sort, order == "desc", page, size)
    return LinesPageResponse(
        items=[_line(row) for row in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
        since=result.since,
        until=result.until,
        kpis=_kpis(result.kpis),
        bands=_bands(service),
        as_of=result.as_of,
    )


@router.put("/lines/reason", response_model=AssignedResponse, dependencies=[_PENDING_GATE])
@inject
async def assign_reasons(
    body: ReasonAssignment,
    principal: Principal = _principal,
    service: ServiceLevelService = _service,
) -> AssignedResponse:
    """Give several pending lines the same reason (``null`` takes it away)."""

    rows = await service.set_reason(principal, body.line_keys, body.reason_id, body.note)
    return AssignedResponse(updated=len(rows), items=[_line(row) for row in rows])


@router.put("/lines/{line_key}/reason", response_model=LineResponse, dependencies=[_PENDING_GATE])
@inject
async def assign_reason(
    line_key: str,
    body: ReasonChoice,
    principal: Principal = _principal,
    service: ServiceLevelService = _service,
) -> LineResponse:
    """Give a pending line its reason (``null`` takes it away), with an optional note."""

    if len(line_key) > 64:
        raise InvalidInputError("Línea inválida")
    rows = await service.set_reason(principal, [line_key], body.reason_id, body.note)
    return _line(rows[0])


@router.get("/dashboard", response_model=DashboardResponse, dependencies=[_EITHER_GATE])
@inject
async def dashboard(
    filters: FilterParams = _filters, service: ServiceLevelService = _service
) -> DashboardResponse:
    """The KPIs of the period (the current month by default), by C.O. and day by day, and the
    pending value by responsible."""

    board = await service.dashboard(filters.build(service))
    return DashboardResponse(
        since=board.since,
        until=board.until,
        kpis=_kpis(board.kpis),
        bands=_bands(service),
        by_co=[
            CoKpisResponse(co=item.group.key, name=item.group.name, kpis=_kpis(item.kpis))
            for item in board.by_co
        ],
        daily=[DayKpisResponse(day=day, kpis=_kpis(item)) for day, item in board.daily],
        by_responsible=[_group(item) for item in board.by_responsible],
        as_of=board.as_of,
        classes=_run(board.classes),
    )


@router.get("/dashboard/breakdown", response_model=BreakdownResponse, dependencies=[_EITHER_GATE])
@inject
async def breakdown(
    dimension: Dimension = _DIMENSION,
    filters: FilterParams = _filters,
    sort: GroupSort = _GROUP_SORT,
    order: Order = _ORDER,
    page: int = _PAGE,
    size: int = _SIZE,
    service: ServiceLevelService = _service,
) -> BreakdownResponse:
    """The lines grouped by supplier, seller, customer, reason, responsible, product, reason ×
    product or ABCD class: value asked, invoiced and pending, and NS Valor."""

    result = await service.breakdown(
        filters.build(service), dimension, sort, order == "desc", page, size
    )
    return BreakdownResponse(
        dimension=result.dimension,
        items=[_group(item) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.get("/pareto", response_model=ParetoResponse, dependencies=[_EITHER_GATE])
@inject
async def pareto(
    kind: ClassKind = _KIND,
    co: str | None = _CO,
    service: ServiceLevelService = _service,
) -> ParetoResponse:
    """The Pareto curve of the requested value of the last classification: % of the products (of
    a C.O. or every one) or customers against % of the value."""

    result = await service.pareto(kind, co or None)
    return ParetoResponse(
        kind=result.kind,
        co=result.co,
        items=result.items,
        points=[ParetoPointResponse(items=p.items, value=p.value) for p in result.points],
        run=_run(result.run),
    )


@router.get("/classes", response_model=ClassStatusResponse, dependencies=[_EITHER_GATE])
@inject
async def class_status(service: ServiceLevelService = _service) -> ClassStatusResponse:
    """The last ABCD classification, its cuts and the months the next one reads."""

    since, until = service.class_window()
    return ClassStatusResponse(
        window_from=since,
        window_to=until,
        cuts=CutsResponse(a=service.cuts.a, b=service.cuts.b, c=service.cuts.c),
        run=_run(await service.class_run()),
    )


@router.post("/classes/recompute", response_model=ClassRunResponse, dependencies=[_PENDING_GATE])
@inject
async def recompute_classes(
    principal: Principal = _principal, service: ServiceLevelService = _service
) -> ClassRunResponse:
    """Classify again now with the last closed months (``pendientes-pedidos: manage``)."""

    return _run(await service.recompute_classes(principal))  # type: ignore[return-value]


__all__ = ["router"]
