"""Endpoints of the supplier supports master."""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.core.errors import ForbiddenError, InvalidInputError
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.supplier_supports.application.costs import SupportCostService
from app.modules.supplier_supports.application.deletions import SupportDeletionService
from app.modules.supplier_supports.application.internal_requests import InternalRequestService
from app.modules.supplier_supports.application.requests import SupportRequestService
from app.modules.supplier_supports.application.rollover import SupportRolloverService
from app.modules.supplier_supports.application.service import (
    MAX_BATCH,
    MAX_PAGE_SIZE,
    SupplierSupportService,
)
from app.modules.supplier_supports.domain.entities import (
    Conflict,
    Issue,
    SupportData,
    SupportView,
    Tier,
)

router = APIRouter()
_READ_GATE = Depends(require_permission("apoyos", "solicitudes-apoyos"))
_service = Depends(Provide[ApplicationContainer.v1_container.supplier_support_service])
_principal = Depends(get_current_principal)
_requests = Depends(Provide[ApplicationContainer.v1_container.supplier_support_request_service])
_internal = Depends(Provide[ApplicationContainer.v1_container.internal_support_request_service])
_costs = Depends(Provide[ApplicationContainer.v1_container.supplier_support_cost_service])
_rollover = Depends(Provide[ApplicationContainer.v1_container.supplier_support_rollover_service])
_deletions = Depends(Provide[ApplicationContainer.v1_container.supplier_support_deletion_service])

Kind = Literal[
    "tactico_cliente",
    "actividad_temporal",
    "lista_especial",
    "rebate",
    "recuperacion_cliente",
    "liquidacion_inventario",
    "presupuesto",
    "excepcion_margen",
]
Funding = Literal["proveedor", "jdh", "mixto"]
Settlement = Literal["nota_credito", "pie_factura", "costo_neto_facturado", "rebate_periodico"]
CalcMethod = Literal[
    "pct_costo",
    "pct_precio_lista",
    "valor_unidad",
    "valor_precio_lista",
    "costo_neto",
    "precio_neto",
    "puntos_margen",
    "pct_ventas",
    "margen_minimo",
]
ValueUnit = Literal["unidad", "caja", "kg"]
Validity = Literal["vigente", "vencido", "programado"]
ThresholdType = Literal["unidades", "cajas", "monto_ventas"]
State = Literal["vigente", "vencido", "programado", "inactivo", "anulado"]
# Filters also take «por vencer»: in force and ending within a month (rows stay vigente).
ValidityFilter = Literal["vigente", "vencido", "programado", "por_vencer"]
StateFilter = Literal["vigente", "vencido", "programado", "por_vencer", "inactivo", "anulado"]
OnOverlap = Literal["vencer_anterior", "anular_anterior"]


class TierBody(BaseModel):
    """A volume or sales threshold and the value it unlocks."""

    threshold_type: ThresholdType
    from_value: Decimal = Field(ge=0)
    to_value: Decimal | None = Field(default=None, ge=0)
    value: Decimal


class IssueResponse(BaseModel):
    """A data problem found while migrating or saving the support."""

    code: str
    detail: str | None
    field: str | None


class _SupportFields(BaseModel):
    """Inputs shared by create and the response."""

    external_ref: str | None = Field(default=None, max_length=200)
    program: str | None = Field(default=None, max_length=200)
    settlement_method: Settlement | None = None
    settlement_frequency: str | None = Field(default=None, max_length=100)
    customer_nit: str | None = Field(default=None, max_length=30)
    customer_branch_id: str | None = Field(default=None, max_length=20)
    customer_name: str | None = Field(default=None, max_length=300)
    customer_group: str | None = Field(default=None, max_length=200)
    product_sku: str | None = Field(default=None, max_length=50)
    supplier_sku: str | None = Field(default=None, max_length=100)
    brand: str | None = Field(default=None, max_length=100)
    regionals: list[str] = Field(default_factory=list, max_length=20)
    business_units: list[str] = Field(default_factory=list, max_length=20)
    channels: list[str] = Field(default_factory=list, max_length=30)
    warehouse: str | None = Field(default=None, max_length=50)
    # Applies also in the promotional warehouses (SIESA group 23); otherwise an order from one
    # of them goes at that warehouse's cost, without the support.
    promo_warehouses: bool = False
    supplier_value: Decimal | None = None
    jdh_pct: Decimal | None = None
    jdh_net_cost: Decimal | None = Field(default=None, ge=0, lt=10**12, decimal_places=6)
    value_unit: ValueUnit | None = None
    target_margin_pct: Decimal | None = None
    min_margin_pct: Decimal | None = None
    list_margin_pct: Decimal | None = None
    # Margin the product may drop to on top of the supplier's support ("margen apoyado").
    supported_margin_pct: Decimal | None = None
    # % Margen Garantizado: the supplier's minimum margin (-1 < margin < guaranteed); the
    # supported price is the cost at it (2026-11-23).
    supplier_min_margin_pct: Decimal | None = None
    pack_factor: Decimal | None = None
    reference_cost: Decimal | None = None
    reference_list_price: Decimal | None = None
    reference_date: date | None = None
    min_quantity: Decimal | None = Field(default=None, ge=0)
    committed_volume: Decimal | None = Field(default=None, ge=0)
    budget_amount: Decimal | None = Field(default=None, ge=0)
    budget_units: Decimal | None = Field(default=None, ge=0)
    units_used: Decimal | None = Field(default=None, ge=0)
    stock_quantity: Decimal | None = Field(default=None, ge=0)
    conditions: str | None = Field(default=None, max_length=2000)
    valid_from: date | None = None
    valid_to: date | None = None
    end_condition: str | None = Field(default=None, max_length=300)
    authorized_by: str | None = Field(default=None, max_length=200)
    supplier_contact: str | None = Field(default=None, max_length=200)
    sales_rep: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class SupportCreate(_SupportFields):
    """A new support. Percentages are fractions (0.05 = 5%)."""

    kind: Kind
    calc_method: CalcMethod
    supplier_code: str | None = Field(
        default=None, max_length=20, description="Commercial supplier code (Cod. Proveedor)"
    )
    funding: Funding = "proveedor"
    source: str | None = Field(default=None, max_length=300)
    source_key: str | None = Field(default=None, max_length=300)
    tiers: list[TierBody] = Field(default_factory=list, max_length=20)
    on_overlap: OnOverlap | None = Field(
        default=None,
        description="When it crosses a live support of the same class and scope: expire "
        "(vencer_anterior) or void (anular_anterior) the previous one; without it, a 409 "
        "lists the supports crossed in `overlaps`",
    )
    overlap_reason: str | None = Field(default=None, max_length=500)


class SupportBatch(BaseModel):
    """Several new supports validated together."""

    items: list[SupportCreate] = Field(min_length=1, max_length=MAX_BATCH)
    on_overlap: OnOverlap | None = None
    overlap_reason: str | None = Field(default=None, max_length=500)


class CustomerBranchFilter(BaseModel):
    """A branch of a customer, for the base search by NIT-sucursal."""

    nit: str = Field(min_length=1, max_length=30)
    branch_id: str = Field(min_length=1, max_length=20)


class RetireItem(BaseModel):
    """A support chosen in the table, at the version the person saw."""

    id: UUID
    version: int | None = Field(default=None, ge=1)


class RetireFilter(BaseModel):
    """Every support a search matches (the body of ``/search`` without paging)."""

    match: Literal["all", "any"] = "all"
    conditions: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    customer_nits: list[str] = Field(default_factory=list, max_length=500)
    customer_branches: list[CustomerBranchFilter] = Field(default_factory=list, max_length=500)
    business_groups: list[str] = Field(default_factory=list, max_length=500)
    supplier_codes: list[str] = Field(default_factory=list, max_length=500)
    product_skus: list[str] = Field(default_factory=list, max_length=500)
    q: str | None = Field(default=None, max_length=100)
    active_on: date | None = None
    validity: ValidityFilter | None = None
    state: StateFilter | None = None
    needs_review: bool | None = None
    has_issues: bool | None = None
    has_conflicts: bool | None = None
    has_attachments: bool | None = None


class SupportRetire(BaseModel):
    """Void or expire several supports at once, with the observation."""

    action: Literal["anular", "vencer"]
    reason: str = Field(min_length=3, max_length=500)
    last_day: date | None = Field(
        default=None, description="Last day in force of an expiry; default yesterday"
    )
    items: list[RetireItem] = Field(default_factory=list, max_length=2000)
    filter: RetireFilter | None = None


class SupportDelete(BaseModel):
    """Delete supports for good (the area leader and platform administrators only)."""

    reason: str = Field(min_length=3, max_length=500)
    items: list[RetireItem] = Field(default_factory=list, max_length=2000)
    filter: RetireFilter | None = None


class DeletedResponse(BaseModel):
    deleted: int


class RetireResponse(BaseModel):
    """What a mass action changed."""

    action: str
    voided: list[UUID]
    expired: list[UUID]


class SupportPatch(_SupportFields):
    """Fields to change; anything omitted is left as it is, ``null`` clears it."""

    version: int = Field(ge=1, description="Version the client edited")
    supplier_code: str | None = Field(default=None, max_length=20)
    kind: Kind | None = None
    calc_method: CalcMethod | None = None
    funding: Funding | None = None
    status: Literal["activo", "pendiente_revision"] | None = None
    # Reversible switch: off = greyed out and out of force, without voiding the support.
    is_enabled: bool = True
    tiers: list[TierBody] | None = Field(default=None, max_length=20)
    on_overlap: OnOverlap | None = None
    overlap_reason: str | None = Field(default=None, max_length=500)


class SupportVoid(BaseModel):
    """Why and at which version a support is voided."""

    version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)


class MoneyPairResponse(BaseModel):
    """A money value per box and per minimum selling unit (empty when not convertible)."""

    box: Decimal | None
    unit: Decimal | None


class DerivedResponse(BaseModel):
    """Amounts and margins computed from the stored inputs."""

    supplier_amount: Decimal | None
    jdh_amount: Decimal | None
    net_cost: Decimal | None
    pct_on_cost: Decimal | None
    pct_on_list: Decimal | None
    suggested_price: Decimal | None
    margin_order: Decimal | None
    margin_recomposed: Decimal | None
    min_margin_pct: Decimal | None
    cost: MoneyPairResponse
    list_price: MoneyPairResponse
    supplier_amount_per: MoneyPairResponse
    net_cost_per: MoneyPairResponse
    suggested_price_per: MoneyPairResponse
    # Which cost the figures use: "dwh" (quoter's rule) or "referencia" (stored); null = none.
    cost_basis: Literal["dwh", "referencia"] | None
    # The product's general margin (margin master) and the margin the supplier's money leaves
    # at the list price (cost at the product's margin).
    product_margin_pct: Decimal | None
    recomposed_list_margin: Decimal | None
    # Rules v2 (per inventory unit). suggested_price = Precio Apoyado Sugerido (list price -
    # supplier_amount); margin_recomposed = (price - cost + support) / price.
    # supported_margin_pct = JDH's authorized minimum margin (Excepción margen mínimo);
    # supported_price = Precio Apoyado Mínimo = net cost / (1 - that margin); the client's
    # discount is how far it falls below the list price.
    supported_margin_pct: Decimal | None
    supported_price: Decimal | None
    supported_price_per: MoneyPairResponse
    # Disabled for now: always null (do not show the client's discount).
    customer_discount_pct: Decimal | None
    # True when the supported cost is above today's DWH cost (or the value is negative): the
    # support brings no money at this cost and the figures count it as 0.
    support_above_cost: bool = False


class DwhCostResponse(BaseModel):
    """The cost of the support's SKU in the DWH with the quoter's rule (daily job)."""

    # Per inventory unit, the higher of quotation and inventory, without the lists' factor.
    unit_cost: Decimal | None
    # "cotizacion" or "inventario": which one won.
    source: str | None
    quote_cost: Decimal | None
    inventory_cost: Decimal | None
    # Warehouses used: the client's price list (or billing center), or list 1's highest.
    plan_key: str
    plan_label: str
    snapshot_date: date | None
    computed_at: datetime | None


class SupportResponse(_SupportFields):
    """A support with resolved names, validity and derived values."""

    id: UUID
    kind: str
    calc_method: str
    funding: str
    status: str
    commercial_supplier_id: UUID | None
    supplier_code: str | None
    supplier_name: str | None
    product_description: str | None
    product_brand: str | None
    validity: Validity
    state: State
    needs_review: bool
    is_enabled: bool
    unit_basis_source: str
    has_conflicts: bool
    attachment_count: int
    # The support's business group, else the customer's in the master (null = none), and
    # the margin list its prices use: margin_list, margin_wholesale, margin_mass_food or
    # margin_market (Food service, Mayoristas, Masivo food, Market Apartadó).
    business_group: str | None = None
    margin_list: str | None = None
    # A business group's support: it covers every customer of ``customer_group`` (also one
    # that joined later), whatever NIT its copy carries.
    group_wide: bool = False
    # In force and ending within a month («por vencer»); its state stays vigente.
    expiring: bool = False
    conflicts: list[UUID]
    issues: list[IssueResponse]
    tiers: list[TierBody]
    derived: DerivedResponse
    dwh_cost: DwhCostResponse | None
    # What wrote the current version (manual, carga, aprobacion_jdh…) and its bulk load.
    last_origin: str | None
    last_load_id: UUID | None
    source: str | None
    source_key: str | None
    void_reason: str | None
    voided_at: datetime | None
    created_by: str
    updated_by: str
    version: int
    created_at: datetime
    updated_at: datetime


class SupportPageResponse(BaseModel):
    """A page of supports."""

    items: list[SupportResponse]
    total: int
    page: int
    size: int


class SearchRequest(BaseModel):
    """Dynamic filtering; ``GET /search/fields`` lists fields and operators."""

    match: Literal["all", "any"] = "all"
    conditions: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    customer_nits: list[str] = Field(
        default_factory=list,
        max_length=500,
        description=(
            "Base search: client NIT(s); their supports for the whole NIT or one of its "
            "branches, never a business group's (that is business_groups)"
        ),
    )
    customer_branches: list[CustomerBranchFilter] = Field(
        default_factory=list,
        max_length=500,
        description="Base search: NIT-sucursal; the supports of those branches",
    )
    business_groups: list[str] = Field(
        default_factory=list,
        max_length=500,
        description=(
            "Base search: business group name(s); the supports loaded for the whole group "
            "(group_wide), once each. Case and spaces do not matter"
        ),
    )
    supplier_codes: list[str] = Field(
        default_factory=list, max_length=500, description="Base search: commercial supplier code(s)"
    )
    product_skus: list[str] = Field(
        default_factory=list, max_length=500, description="Base search: JDH reference(s)"
    )
    q: str | None = Field(default=None, max_length=100)
    active_on: date | None = Field(default=None, description="Day for `validity`; default today")
    validity: ValidityFilter | None = None
    state: StateFilter | None = Field(
        default=None,
        description=(
            "The state people see: vigente, vencido, programado, inactivo or anulado; or"
            " por_vencer (vigente and ending within a month)"
        ),
    )
    needs_review: bool | None = None
    has_issues: bool | None = None
    has_conflicts: bool | None = None
    has_attachments: bool | None = Field(
        default=None,
        description="Only supports with (true) or without (false) a supporting document",
    )
    effective_only: bool = Field(
        default=False, description="Keep the effective support per supplier x client x SKU"
    )
    include_voided: bool = False
    sort: list[str] = Field(default_factory=list, max_length=5)
    page: int = Field(default=1, ge=1)
    size: int = Field(default=50, ge=1, le=MAX_PAGE_SIZE)


class ConflictResponse(BaseModel):
    """Another support in force over the same scope."""

    id: UUID
    valid_from: date | None
    valid_to: date | None
    program: str | None
    supplier_value: Decimal | None
    calc_method: str
    status: str


_DATA_FIELDS = tuple(item.name for item in dataclass_fields(SupportData))


def _data(body: SupportCreate) -> SupportData:
    values = {
        name: getattr(body, name)
        for name in _DATA_FIELDS
        if name not in {"issues", "tiers", "commercial_supplier_id", "product_id", "status"}
        and hasattr(body, name)
    }
    for name in ("regionals", "business_units", "channels"):
        values[name] = tuple(values[name])
    values["tiers"] = _tiers(body.tiers)
    return SupportData(**values)


def _tiers(items: list[TierBody]) -> tuple[Tier, ...]:
    return tuple(
        Tier(item.threshold_type, item.from_value, item.to_value, item.value) for item in items
    )


_HIDDEN_COST_FIELDS = {"reference_cost": None, "jdh_net_cost": None}
_HIDDEN_DERIVED = ("cost", "net_cost_per")


def _commercial(response: SupportResponse) -> SupportResponse:
    """What Comercial sees: list price and supported prices, never the cost (BI only)."""

    empty = MoneyPairResponse(box=None, unit=None)
    derived = response.derived.model_copy(
        update={
            **dict.fromkeys(_HIDDEN_DERIVED, empty),
            "net_cost": None,
            "margin_order": None,
            "cost_basis": None,
        }
    )
    return response.model_copy(update={**_HIDDEN_COST_FIELDS, "dwh_cost": None, "derived": derived})


def _shown(view: SupportView, sees_cost: bool) -> SupportResponse:
    response = _response(view)
    return response if sees_cost else _commercial(response)


def _response(view: SupportView) -> SupportResponse:
    support, data = view.support, view.support.data
    values: dict[str, Any] = {
        name: getattr(data, name)
        for name in _DATA_FIELDS
        if name not in {"issues", "tiers", "commercial_supplier_id", "product_id"}
    }
    for name in ("regionals", "business_units", "channels"):
        values[name] = list(values[name])
    return SupportResponse(
        **values,
        id=support.id,
        commercial_supplier_id=data.commercial_supplier_id,
        supplier_code=view.supplier_code,
        supplier_name=view.supplier_name,
        product_description=view.product_description,
        product_brand=view.product_brand,
        validity=view.validity,  # type: ignore[arg-type]
        state=view.state,  # type: ignore[arg-type]
        needs_review=view.needs_review,
        has_conflicts=view.has_conflicts,
        attachment_count=view.attachment_count,
        business_group=view.business_group,
        margin_list=view.margin_list,
        expiring=view.expiring,
        conflicts=list(view.conflicts),
        issues=[_issue(issue) for issue in data.issues],
        tiers=[
            TierBody(
                threshold_type=tier.threshold_type,  # type: ignore[arg-type]
                from_value=tier.from_value,
                to_value=tier.to_value,
                value=tier.value,
            )
            for tier in data.tiers
        ],
        derived=DerivedResponse.model_validate(view.derived, from_attributes=True),
        dwh_cost=(
            DwhCostResponse.model_validate(view.dwh_cost, from_attributes=True)
            if view.dwh_cost
            else None
        ),
        last_origin=support.last_origin,
        last_load_id=support.last_load_id,
        void_reason=support.void_reason,
        voided_at=support.voided_at,
        created_by=support.created_by,
        updated_by=support.updated_by,
        version=support.version,
        created_at=support.created_at,
        updated_at=support.updated_at,
    )


def _issue(issue: Issue) -> IssueResponse:
    return IssueResponse(code=issue.code, detail=issue.detail, field=issue.field)


@router.post(
    "/search",
    dependencies=[_READ_GATE],
    response_model=SupportPageResponse,
    summary="Search supports",
)
@inject
async def search_supports(
    body: SearchRequest,
    principal: Principal = _principal,
    service: SupplierSupportService = _service,
) -> SupportPageResponse:
    """Filter supports with a dynamic tree of conditions, free text, validity and sorting.
    The cost is only for BI (managers of ``apoyos``); Comercial gets it empty. Only the
    caller's customers come: BI, Comercial's leaders and admins see every support; a
    salesperson the supports of their customers and the general ones (no NIT); without
    customers, none (``customer_scope`` in ``/permissions``)."""

    page = await service.search(principal, **body.model_dump())
    sees_cost = await service.can_manage(principal)
    return SupportPageResponse(
        items=[_shown(view, sees_cost) for view in page.items],
        total=page.total,
        page=page.page,
        size=page.size,
    )


class StateCountsResponse(BaseModel):
    """How many supports of a search are in each state (the KPIs of the master)."""

    vigente: int
    por_vencer: int = Field(description="Part of vigente: in force and ending within a month")
    programado: int
    vencido: int
    inactivo: int
    total: int = Field(description="Every support of the search but the voided ones")


@router.post(
    "/summary",
    dependencies=[_READ_GATE],
    response_model=StateCountsResponse,
    summary="Count the supports of a search by state",
)
@inject
async def summarize_supports(
    body: SearchRequest,
    principal: Principal = _principal,
    service: SupplierSupportService = _service,
) -> StateCountsResponse:
    """The body of ``/search`` (paging and sorting ignored): the same filters and the same
    customers of the caller, counted by state whatever ``state`` and ``validity`` say, so
    each count is what that chip would list on ``active_on`` (default today)."""

    return StateCountsResponse(**await service.summary(principal, **body.model_dump()))


@router.get("/search/fields", dependencies=[_READ_GATE], summary="Filterable fields and operators")
@inject
async def search_fields(
    _: Principal = _principal,
    service: SupplierSupportService = _service,
) -> list[dict[str, Any]]:
    """Describe every field a search condition or sort key may use."""

    return service.search_fields()


@router.get("/permissions", summary="What the caller may do")
@inject
async def permissions(
    principal: Principal = _principal,
    service: SupportRequestService = _requests,
    internal: InternalRequestService = _internal,
    supports: SupplierSupportService = _service,
    deletions: SupportDeletionService = _deletions,
) -> dict[str, bool | str]:
    """Tell the UI whether to offer write actions (create, edit, switch, void), and the
    internal support requests: ``can_review_internal`` (BI or a designated reviewer) and
    ``can_request_internal`` (whoever quotes or asks JDH supports: "my requests").
    ``can_delete``: deletes supports in bulk and support requests for good (the leader of
    the supports' area and the platform administrators only, 2026-11-23).
    ``customer_scope`` says which supports the search shows: ``all``, ``team``, ``mine``
    (their customers and the general ones) or ``none`` (no customers: no supports)."""

    return {
        **await service.permissions(principal),
        **await internal.permissions(principal),
        "can_delete": await deletions.can_delete(principal),
        "customer_scope": await supports.customer_scope(principal),
    }


@router.get("/catalogs", dependencies=[_READ_GATE], summary="Vocabularies and scope values in use")
@inject
async def catalogs(
    _: Principal = _principal,
    service: SupplierSupportService = _service,
) -> dict[str, Any]:
    """Closed vocabularies (classes and their types, statuses, labels) and the regions, units
    and channels in use."""

    return await service.catalogs()


class AlertSupport(BaseModel):
    """A support as an alert shows it."""

    id: UUID
    version: int
    kind: str
    calc_method: str
    customer_nit: str | None
    customer_name: str | None
    product_sku: str | None
    supplier_sku: str | None
    supplier_value: Decimal | None
    valid_from: date | None
    valid_to: date | None


class ReplacementAlert(BaseModel):
    """A programmed support and the one in force it will replace."""

    programmed: AlertSupport
    previous: AlertSupport
    # The previous one's last day when the programmed one starts.
    previous_ends: date
    # True: the previous one already ends that day; false: the daily job will cut it.
    chained: bool


def _alert_support(support: Any) -> AlertSupport:
    data = support.data
    return AlertSupport(
        id=support.id,
        version=support.version,
        kind=data.kind,
        calc_method=data.calc_method,
        customer_nit=data.customer_nit,
        customer_name=data.customer_name,
        product_sku=data.product_sku,
        supplier_sku=data.supplier_sku,
        supplier_value=data.supplier_value,
        valid_from=data.valid_from,
        valid_to=data.valid_to,
    )


@router.get(
    "/alerts",
    dependencies=[_READ_GATE],
    response_model=list[ReplacementAlert],
    summary="Programmed supports that will replace one in force",
)
@inject
async def replacement_alerts(
    principal: Principal = _principal,
    service: SupplierSupportService = _service,
    rollover: SupportRolloverService = _rollover,
) -> list[ReplacementAlert]:
    """Each programmed support with the one in force it replaces. BI decides: leave it
    programmed (the previous one ends the day before; the daily job cuts it if needed) or
    expire the previous one now with ``POST /retire`` (``vencer``). Managers only."""

    if not await service.can_manage(principal):
        raise ForbiddenError("No tienes permiso para gestionar apoyos de proveedores")
    return [
        ReplacementAlert(
            programmed=_alert_support(item.programmed),
            previous=_alert_support(item.previous),
            previous_ends=item.previous_ends,
            chained=item.chained,
        )
        for item in await rollover.replacements()
    ]


class BusinessGroupResponse(BaseModel):
    """A business group of the customer master and its active customers."""

    name: str
    customers: int


@router.get(
    "/business-groups",
    dependencies=[_READ_GATE],
    response_model=list[BusinessGroupResponse],
    summary="Business groups by name",
)
@inject
async def business_groups(
    q: str = Query(min_length=2, max_length=100),
    _: Principal = _principal,
    service: SupplierSupportService = _service,
) -> list[BusinessGroupResponse]:
    """Business groups whose name contains ``q`` (the code is not known), with how many active
    customers a support by group would reach."""

    return [
        BusinessGroupResponse(name=name, customers=count)
        for name, count in await service.business_groups(q)
    ]


@router.post(
    "/by-group",
    dependencies=[_READ_GATE],
    response_model=list[SupportResponse],
    status_code=201,
    summary="Create one support per customer of a business group",
)
@inject
async def create_by_group(
    body: SupportCreate,
    background: BackgroundTasks,
    principal: Principal = _principal,
    service: SupplierSupportService = _service,
    costs: SupportCostService = _costs,
) -> list[SupportResponse]:
    """``customer_group`` names the group and ``customer_nit`` stays empty: one support per
    active customer of the group, each with the group's name. All or none. Managers only."""

    views = await service.create_for_group(
        principal,
        _data(body),
        supplier_code=body.supplier_code,
        on_overlap=body.on_overlap,
        overlap_reason=body.overlap_reason,
    )
    background.add_task(costs.refresh, [view.support.id for view in views])
    return [_response(view) for view in views]


@router.post(
    "",
    dependencies=[_READ_GATE],
    response_model=SupportResponse,
    status_code=201,
    summary="Create a support",
)
@inject
async def create_support(
    body: SupportCreate,
    background: BackgroundTasks,
    principal: Principal = _principal,
    service: SupplierSupportService = _service,
    costs: SupportCostService = _costs,
) -> SupportResponse:
    """Create a support bound to a real supplier, product and client. Managers only."""

    view = await service.create(
        principal,
        _data(body),
        supplier_code=body.supplier_code,
        on_overlap=body.on_overlap,
        overlap_reason=body.overlap_reason,
    )
    background.add_task(costs.refresh, [view.support.id])
    return _response(view)


@router.post(
    "/preview",
    dependencies=[_READ_GATE],
    response_model=SupportResponse,
    summary="Figures of a support before saving it",
)
@inject
async def preview_support(
    body: SupportCreate,
    principal: Principal = _principal,
    service: SupplierSupportService = _service,
    costs: SupportCostService = _costs,
) -> SupportResponse:
    """The creation form's figures: DWH cost (the quoter's rule), list price at the
    customer's optimal margin, Precio Apoyado Sugerido and Precio Apoyado Mínimo. The same
    checks as creating (422 names the field); nothing is saved. Managers only."""

    view = await service.preview(
        principal, _data(body), supplier_code=body.supplier_code, cost=costs.cost_of
    )
    return _response(view)


@router.post(
    "/batch",
    dependencies=[_READ_GATE],
    response_model=list[SupportResponse],
    status_code=201,
    summary="Create supports",
)
@inject
async def create_supports(
    body: SupportBatch,
    background: BackgroundTasks,
    principal: Principal = _principal,
    service: SupplierSupportService = _service,
    costs: SupportCostService = _costs,
) -> list[SupportResponse]:
    """Create up to 500 supports atomically-validated (all checked before any is saved)."""

    views = await service.create_many(
        principal,
        [(_data(item), item.supplier_code) for item in body.items],
        on_overlap=body.on_overlap,
        overlap_reason=body.overlap_reason,
    )
    background.add_task(costs.refresh, [view.support.id for view in views])
    return [_response(view) for view in views]


@router.post(
    "/retire",
    dependencies=[_READ_GATE],
    response_model=RetireResponse,
    summary="Void or expire supports in bulk",
)
@inject
async def retire_supports(
    body: SupportRetire,
    principal: Principal = _principal,
    service: SupplierSupportService = _service,
) -> RetireResponse:
    """Void (``anular``) or expire (``vencer``) the supports chosen (``items``) or every one
    a search matches (``filter``), up to 2,000, with the observation. All or none; a stale
    ``version`` or an already voided support is a 409. Managers only."""

    if not body.items and body.filter is None:
        raise InvalidInputError("Elige los apoyos o un filtro")
    changed = await service.retire_many(
        principal,
        body.action,
        body.reason,
        items=[(item.id, item.version) for item in body.items],
        criteria=body.filter.model_dump() if body.filter is not None else None,
        last_day=body.last_day,
    )
    return RetireResponse(
        action=body.action,
        voided=[item.id for item in changed if item.data.status == "anulado"],
        expired=[item.id for item in changed if item.data.status != "anulado"],
    )


@router.post(
    "/delete",
    dependencies=[_READ_GATE],
    response_model=DeletedResponse,
    summary="Delete supports for good, in bulk",
)
@inject
async def delete_supports(
    body: SupportDelete,
    principal: Principal = _principal,
    deletions: SupportDeletionService = _deletions,
) -> DeletedResponse:
    """Delete the supports chosen (``items``) or every one a search matches (``filter``,
    voided ones included), up to 2,000, with their requests, JDH history, costs, tiers and
    links to documents. All or none; a stale ``version`` is a 409. The warehouse cannot
    delete: each one not voided yet is published as voided first. Only the leader of the
    supports' area (BI) and the platform administrators (403 otherwise)."""

    if not body.items and body.filter is None:
        raise InvalidInputError("Elige los apoyos o un filtro")
    return DeletedResponse(
        deleted=await deletions.delete_supports(
            principal,
            {item.id: item.version for item in body.items},
            body.filter.model_dump() if body.filter is not None else None,
            body.reason,
        )
    )


@router.get(
    "/{support_id}",
    dependencies=[_READ_GATE],
    response_model=SupportResponse,
    summary="Get a support",
)
@inject
async def get_support(
    support_id: UUID,
    principal: Principal = _principal,
    service: SupplierSupportService = _service,
) -> SupportResponse:
    """Return one support with names, validity, conflicts and derived values (the cost only
    for BI)."""

    return _shown(await service.get(support_id, principal), await service.can_manage(principal))


@router.patch(
    "/{support_id}",
    dependencies=[_READ_GATE],
    response_model=SupportResponse,
    summary="Update a support",
)
@inject
async def update_support(
    support_id: UUID,
    body: SupportPatch,
    background: BackgroundTasks,
    principal: Principal = _principal,
    service: SupplierSupportService = _service,
    costs: SupportCostService = _costs,
) -> SupportResponse:
    """Change only the fields sent. A stale ``version`` is a 409. Managers only."""

    sent = body.model_fields_set - {"version", "supplier_code", "on_overlap", "overlap_reason"}
    changes: dict[str, Any] = {name: getattr(body, name) for name in sent}
    if "tiers" in changes:
        changes["tiers"] = _tiers(body.tiers or [])
    view = await service.update(
        principal,
        support_id,
        expected_version=body.version,
        changes=changes,
        supplier_code=body.supplier_code,
        on_overlap=body.on_overlap,
        overlap_reason=body.overlap_reason,
    )
    if sent & {"product_sku", "supplier_sku", "customer_nit"}:
        background.add_task(costs.refresh, [view.support.id])
    return _response(view)


@router.post(
    "/{support_id}/void",
    dependencies=[_READ_GATE],
    response_model=SupportResponse,
    summary="Void a support",
)
@inject
async def void_support(
    support_id: UUID,
    body: SupportVoid,
    principal: Principal = _principal,
    service: SupplierSupportService = _service,
) -> SupportResponse:
    """Void a support with a reason; it stays in the history. Managers only."""

    view = await service.void(
        principal, support_id, expected_version=body.version, reason=body.reason
    )
    return _response(view)


@router.get(
    "/{support_id}/conflicts",
    dependencies=[_READ_GATE],
    response_model=list[ConflictResponse],
    summary="Supports overlapping this one",
)
@inject
async def support_conflicts(
    support_id: UUID,
    _: Principal = _principal,
    service: SupplierSupportService = _service,
) -> list[ConflictResponse]:
    """List live supports over the same scope and period."""

    conflicts: list[Conflict] = await service.conflicts(support_id)
    return [ConflictResponse.model_validate(item, from_attributes=True) for item in conflicts]


__all__ = ["router"]
