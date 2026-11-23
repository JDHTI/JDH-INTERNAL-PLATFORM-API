"""Endpoints of the commercial masters (suppliers, customers, products)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.commercial_masters.application.employee_prices import EmployeePriceService
from app.modules.commercial_masters.application.price_increases import (
    MAX_PAGE_SIZE as PRICE_INCREASES_MAX_PAGE,
)
from app.modules.commercial_masters.application.price_increases import PriceIncreaseService
from app.modules.commercial_masters.application.service import (
    MAX_PAGE_SIZE,
    CommercialMasterService,
    LiquorTax,
)
from app.modules.commercial_masters.domain.entities import Product
from app.modules.commercial_masters.domain.margins import MARGIN_FIELDS, MarginReport
from app.modules.commercial_masters.domain.price_increases import (
    STATUS_LABELS as PRICE_STATUS_LABELS,
)
from app.modules.commercial_masters.domain.price_increases import (
    PriceIncrease,
    PriceIncreaseFilters,
)
from app.modules.commercial_masters.infrastructure.margins_file import template_workbook
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal

router = APIRouter(
    dependencies=[
        Depends(
            require_permission(
                "maestros",
                "apoyos",
                "cotizador",
                "actualizacion-listas",
                "validador-pedidos",
                # Costos: the supplier search and the price increase history.
                "analisis-entradas",
                "novedades-compras",
                "alzas-proveedor",
                "acumulado-alzas",
            )
        )
    ]
)
_service = Depends(Provide[ApplicationContainer.v1_container.commercial_master_service])
_increases = Depends(Provide[ApplicationContainer.v1_container.price_increase_service])
_employee_prices = Depends(Provide[ApplicationContainer.v1_container.employee_price_service])
_principal = Depends(get_current_principal)


class PageResponse[T](BaseModel):
    """A page of results."""

    items: list[T]
    total: int
    page: int
    size: int


class CommercialSupplierNitResponse(BaseModel):
    """A legal NIT a commercial supplier is bought from."""

    nit: str
    product_links: int
    is_primary: bool
    confirmed: bool


class CommercialSupplierResponse(BaseModel):
    """ERP supplier code that groups products."""

    id: UUID
    code: str
    name: str
    is_active: bool
    product_count: int
    active_product_count: int
    nits: list[CommercialSupplierNitResponse]


class CommercialSupplierUpdate(BaseModel):
    """Editable data of a commercial supplier; omitted fields are untouched.

    The margin no longer lives on the supplier: it is set on each product.
    """

    confirmed_nits: list[str] | None = Field(default=None, max_length=50)
    primary_nit: str | None = Field(default=None, max_length=20)


class LegalSupplierResponse(BaseModel):
    """Legal supplier by branch."""

    id: UUID
    nit: str
    branch_id: str | None
    legal_name: str | None
    branch_name: str | None
    supplier_class: str | None
    supplier_type: str | None
    city: str | None
    payment_condition: str | None
    state: str | None
    branch_state: str | None
    is_active: bool


class CustomerResponse(BaseModel):
    """Client by branch."""

    id: UUID
    nit: str
    branch_id: str | None
    legal_name: str | None
    branch_name: str | None
    establishment_name: str | None
    channel: str | None
    business_unit: str | None
    business_group: str | None
    billing_center: str | None
    logistics_zone: str | None
    department: str | None
    city: str | None
    salesperson_name: str | None
    state: str | None
    branch_state: str | None
    is_active: bool


class ProductResponse(BaseModel):
    """Product; ``sku`` is the JDH reference."""

    id: UUID
    sku: str
    supplier_sku: str | None
    description: str | None
    state: str | None
    is_active: bool
    commercial_supplier_code: str | None
    # Name of the commercial supplier of that code; null when the code is not in the master.
    commercial_supplier_name: str | None = None
    manufacturer: str | None
    brand: str | None
    category: str | None
    subcategory: str | None
    product_line: str | None
    product_subline: str | None
    product_group: str | None
    pack_factor: Decimal | None
    inventory_unit: str | None
    main_barcode: str | None
    # Sales VAT in percentage points (19 = 19 %), the unit of the quoter line's vat_pct.
    vat_pct: Decimal | None = None
    margin_list: Decimal | None
    margin_wholesale: Decimal | None
    margin_mass_food: Decimal | None
    margin_market: Decimal | None
    # Margen Empleado JDH: the employee price list (MDE1 cost), not a channel margin.
    margin_employee: Decimal | None
    margin_notes: str | None
    margin_source: str | None
    margin_updated_by: str | None
    margin_updated_at: datetime | None
    has_margin: bool
    liquor_tax: Decimal | None
    liquor_tax_coast: Decimal | None
    liquor_tax_group: str | None


# The Salsamentarias margin was removed on 2026-11-23. The edit bodies still accept the field
# while the UI deploys, and it is never written.
_SALSAMENTARIAS_DEPRECATED = (
    "Deprecado: el margen Salsamentarias se eliminó el 2026-11-23 (esos clientes usan el margen "
    "Mayoristas). Se acepta y se ignora."
)
_IGNORED_FIELDS = frozenset({"margin_salsamentarias"})


class ProductUpdate(BaseModel):
    """Fields of a product to change; omitted ones stay, ``null`` clears a margin or a text.

    Margins are fractions (0.18 = 18 %). Warehouse fields (state, supplier) are not editable
    and the others are overwritten by the next sync; margins and the liquor tax are not.
    ``margin_salsamentarias`` is deprecated: accepted and ignored.
    """

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, max_length=300)
    supplier_sku: str | None = Field(default=None, max_length=100)
    manufacturer: str | None = Field(default=None, max_length=200)
    brand: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=200)
    subcategory: str | None = Field(default=None, max_length=200)
    product_line: str | None = Field(default=None, max_length=200)
    product_subline: str | None = Field(default=None, max_length=200)
    product_group: str | None = Field(default=None, max_length=200)
    pack_factor: Decimal | None = Field(default=None, ge=0, le=1_000_000)
    inventory_unit: str | None = Field(default=None, max_length=20)
    main_barcode: str | None = Field(default=None, max_length=50)
    margin_list: Decimal | None = Field(default=None, gt=0, lt=1)
    margin_wholesale: Decimal | None = Field(default=None, gt=0, lt=1)
    margin_mass_food: Decimal | None = Field(default=None, gt=0, lt=1)
    margin_market: Decimal | None = Field(default=None, gt=0, lt=1)
    margin_employee: Decimal | None = Field(default=None, gt=0, lt=1)
    margin_notes: str | None = Field(default=None, max_length=1000)
    liquor_tax: Decimal | None = Field(default=None, ge=0)
    liquor_tax_coast: Decimal | None = Field(default=None, ge=0)
    liquor_tax_group: str | None = Field(default=None, max_length=20)
    margin_salsamentarias: Decimal | None = Field(
        default=None, deprecated=True, description=_SALSAMENTARIAS_DEPRECATED
    )


_Margin = Annotated[Decimal, Field(gt=0, lt=1)]


class LiquorTaxRequest(BaseModel):
    """Liquor consumption tax per unit, in pesos (``IMPTOLIC``)."""

    model_config = ConfigDict(extra="forbid")

    national: Decimal = Field(ge=0, description="Impuesto licor Nacional")
    coast: Decimal = Field(ge=0, description="Impuesto licor Costa")
    group: str = Field(min_length=1, max_length=20, description="Grupo impositivo (0503, 0509)")


class ProductMarginsRequest(BaseModel):
    """Every margin of a product, set by hand. All fields are required but the employee one.

    Margins are fractions (0.18 = 18 %). ``notes`` may be ``null``. ``liquor_tax`` is
    ``null`` for a product that does not pay it; a product that already pays it must send it.
    ``margin_employee`` (Margen Empleado JDH) may be left out to keep the product's.
    ``margin_salsamentarias`` is deprecated: accepted and ignored.
    """

    model_config = ConfigDict(extra="forbid")

    margin_list: _Margin
    margin_wholesale: _Margin
    margin_mass_food: _Margin
    margin_market: _Margin
    margin_employee: _Margin | None = None
    notes: str | None = Field(max_length=1000)
    liquor_tax: LiquorTaxRequest | None
    margin_salsamentarias: Decimal | None = Field(
        default=None, deprecated=True, description=_SALSAMENTARIAS_DEPRECATED
    )


class MarginChangeResponse(BaseModel):
    """What a file would change in one product."""

    sku: str
    description: str | None
    margins: dict[str, dict[str, Decimal | None]]
    notes: dict[str, str | None] | None
    # Liquor tax by regional: liquor_tax (Nacional), liquor_tax_coast (Costa), liquor_tax_group.
    taxes: dict[str, dict[str, Decimal | str | None]] = Field(default_factory=dict)


class MarginIssueResponse(BaseModel):
    """A row of the file that was not applied."""

    row_number: int
    sku: str | None
    problem: str


class MarginReportResponse(BaseModel):
    """Result of a margin update, simulated or applied."""

    applied: bool
    rows_read: int
    updated: int
    unchanged: int
    duplicated: int
    unknown_skus: list[str]
    issues: list[MarginIssueResponse]
    changes: list[MarginChangeResponse]
    changes_truncated: bool


def _product(product: Product) -> ProductResponse:
    margins = product.margins
    return ProductResponse(
        id=product.id,
        sku=product.sku,
        supplier_sku=product.supplier_sku,
        description=product.description,
        state=product.state,
        is_active=product.is_active,
        commercial_supplier_code=product.commercial_supplier_code,
        commercial_supplier_name=product.commercial_supplier_name,
        manufacturer=product.manufacturer,
        brand=product.brand,
        category=product.category,
        subcategory=product.subcategory,
        product_line=product.product_line,
        product_subline=product.product_subline,
        product_group=product.product_group,
        pack_factor=product.pack_factor,
        inventory_unit=product.inventory_unit,
        main_barcode=product.main_barcode,
        vat_pct=product.sales_vat_pct,
        margin_list=margins.list,
        margin_wholesale=margins.wholesale,
        margin_mass_food=margins.mass_food,
        margin_market=margins.market,
        margin_employee=margins.employee,
        margin_notes=margins.notes,
        margin_source=margins.source,
        margin_updated_by=margins.updated_by,
        margin_updated_at=margins.updated_at,
        has_margin=margins.defined,
        liquor_tax=product.liquor_tax,
        liquor_tax_coast=product.liquor_tax_coast,
        liquor_tax_group=product.liquor_tax_group,
    )


def _report(report: MarginReport) -> MarginReportResponse:
    return MarginReportResponse(
        applied=report.applied,
        rows_read=report.rows_read,
        updated=report.updated,
        unchanged=report.unchanged,
        duplicated=report.duplicated,
        unknown_skus=list(report.unknown_skus),
        issues=[
            MarginIssueResponse(row_number=item.row_number, sku=item.sku, problem=item.problem)
            for item in report.issues
        ],
        changes=[
            MarginChangeResponse(
                sku=item.sku,
                description=item.description,
                margins={
                    name: {"from": before, "to": after}
                    for name, (before, after) in item.changes.items()
                },
                notes=None if item.notes is None else {"from": item.notes[0], "to": item.notes[1]},
                taxes={
                    name: {"from": before, "to": after}
                    for name, (before, after) in item.taxes.items()
                },
            )
            for item in report.changes
        ],
        changes_truncated=report.changes_truncated,
    )


_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_QUERY = Query(default=None, max_length=100)
_PAGE = Query(default=1, ge=1)
_SIZE = Query(default=50, ge=1, le=MAX_PAGE_SIZE)


@router.get(
    "/suppliers",
    response_model=PageResponse[CommercialSupplierResponse],
    summary="List commercial suppliers",
)
@inject
async def list_commercial_suppliers(
    q: str | None = _QUERY,
    is_active: bool | None = None,
    page: int = _PAGE,
    size: int = _SIZE,
    _: Principal = _principal,
    service: CommercialMasterService = _service,
) -> PageResponse[CommercialSupplierResponse]:
    """Search commercial suppliers (ERP supplier codes) by code or name."""

    result = await service.list_commercial_suppliers(q, is_active, page, size)
    return PageResponse(
        items=[
            CommercialSupplierResponse.model_validate(item, from_attributes=True)
            for item in result.items
        ],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.get(
    "/suppliers/{code}",
    response_model=CommercialSupplierResponse,
    summary="Get a commercial supplier",
)
@inject
async def get_commercial_supplier(
    code: str,
    _: Principal = _principal,
    service: CommercialMasterService = _service,
) -> CommercialSupplierResponse:
    """Return one commercial supplier with its suggested and confirmed NITs."""

    supplier = await service.get_commercial_supplier(code)
    return CommercialSupplierResponse.model_validate(supplier, from_attributes=True)


@router.patch(
    "/suppliers/{code}",
    response_model=CommercialSupplierResponse,
    summary="Update a commercial supplier",
)
@inject
async def update_commercial_supplier(
    code: str,
    body: CommercialSupplierUpdate,
    principal: Principal = _principal,
    service: CommercialMasterService = _service,
) -> CommercialSupplierResponse:
    """Confirm legal NITs and choose the primary one. Managers only."""

    supplier = await service.update_commercial_supplier(
        principal,
        code,
        fields=frozenset(body.model_fields_set),
        confirmed_nits=body.confirmed_nits,
        primary_nit=body.primary_nit,
    )
    return CommercialSupplierResponse.model_validate(supplier, from_attributes=True)


@router.get(
    "/legal-suppliers",
    response_model=PageResponse[LegalSupplierResponse],
    summary="List legal suppliers",
)
@inject
async def list_legal_suppliers(
    q: str | None = _QUERY,
    nit: str | None = Query(default=None, max_length=20),
    is_active: bool | None = None,
    page: int = _PAGE,
    size: int = _SIZE,
    _: Principal = _principal,
    service: CommercialMasterService = _service,
) -> PageResponse[LegalSupplierResponse]:
    """Search legal suppliers by name or NIT; active ones come first."""

    result = await service.list_legal_suppliers(q, nit, is_active, page, size)
    return PageResponse(
        items=[
            LegalSupplierResponse.model_validate(item, from_attributes=True)
            for item in result.items
        ],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.get("/customers", response_model=PageResponse[CustomerResponse], summary="List customers")
@inject
async def list_customers(
    q: str | None = _QUERY,
    nit: str | None = Query(default=None, max_length=20),
    channel: str | None = Query(default=None, max_length=100),
    business_unit: str | None = Query(default=None, max_length=100),
    business_group: str | None = Query(default=None, max_length=150),
    is_active: bool | None = None,
    page: int = _PAGE,
    size: int = _SIZE,
    _: Principal = _principal,
    service: CommercialMasterService = _service,
) -> PageResponse[CustomerResponse]:
    """Search customers by name or NIT, channel, unit, group and active flag; active ones first."""

    result = await service.list_customers(
        q, nit, channel, business_unit, business_group, is_active, page, size
    )
    return PageResponse(
        items=[
            CustomerResponse.model_validate(item, from_attributes=True) for item in result.items
        ],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.get("/products", response_model=PageResponse[ProductResponse], summary="List products")
@inject
async def list_products(
    q: str | None = _QUERY,
    brand: str | None = Query(default=None, max_length=100),
    category: str | None = Query(default=None, max_length=100),
    commercial_supplier_code: str | None = Query(default=None, max_length=20),
    is_active: bool | None = None,
    has_margin: bool | None = Query(
        default=None, description="Only products with (true) or without (false) a margin"
    ),
    has_consumption_tax: bool | None = Query(
        default=None,
        description="Only products that pay (true) or do not pay (false) the liquor consumption "
        "tax: Nacional or Costa above 0",
    ),
    page: int = _PAGE,
    size: int = _SIZE,
    _: Principal = _principal,
    service: CommercialMasterService = _service,
) -> PageResponse[ProductResponse]:
    """Search products by SKU, description, brand, category, supplier, active flag, margin and
    liquor consumption tax. ``category`` and ``commercial_supplier_code`` take the values of
    ``/products/options``.

    Active products come first, then the inactive ones.
    """

    result = await service.list_products(
        q,
        brand,
        category,
        commercial_supplier_code,
        is_active,
        page,
        size,
        has_margin,
        has_consumption_tax=has_consumption_tax,
    )
    return PageResponse(
        items=[_product(item) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


class ProductSupplierOptionResponse(BaseModel):
    code: str
    name: str | None


class ProductOptionsResponse(BaseModel):
    """What the product master's dropdowns offer."""

    # Commercial suppliers present in the products, by name then code.
    suppliers: list[ProductSupplierOptionResponse]
    # Categories present in the products, sorted.
    categories: list[str]


@router.get(
    "/products/options",
    response_model=ProductOptionsResponse,
    summary="Suppliers and categories of the products",
)
@inject
async def product_options(
    _: Principal = _principal,
    service: CommercialMasterService = _service,
) -> ProductOptionsResponse:
    """The commercial suppliers (code and name) and the categories present in the products: the
    supplier and category dropdowns of the product master."""

    result = await service.product_options()
    return ProductOptionsResponse(
        suppliers=[ProductSupplierOptionResponse(**asdict(item)) for item in result.suppliers],
        categories=result.categories,
    )


class EmployeePriceResponse(BaseModel):
    """One line of the JDH employee price list (pesos per inventory unit)."""

    sku: str
    description: str | None
    brand: str | None
    inventory_unit: str | None
    cost: Decimal
    margin: Decimal
    price: Decimal
    # 19, 5 or 0 (SIESA tax group without VAT); null only when the group carries VAT and
    # SIESA has no rate for it.
    vat_pct: Decimal | None
    price_with_vat: Decimal
    tax_group_id: str | None
    tax_group: str | None


class EmployeePriceListResponse(BaseModel):
    """The employee price list: every active product with an MDE1 cost."""

    warehouse: str
    generated_at: datetime
    # Day of the inventory photo the costs come from.
    snapshot_date: date | None
    total: int
    # Active products left out because MDE1 has no usable cost for them.
    without_cost: int
    # Lines whose VAT rate is unknown (vat_pct null).
    unknown_vat: int
    items: list[EmployeePriceResponse]


@router.get(
    "/products/employee-prices",
    response_model=EmployeePriceListResponse,
    summary="JDH employee price list",
)
@inject
async def employee_prices(
    _: Principal = _principal,
    service: EmployeePriceService = _employee_prices,
) -> EmployeePriceListResponse:
    """Every active product priced at its Margen Empleado JDH over the MDE1 average cost.

    ``price = cost / (1 - margin)`` and ``price_with_vat = price * (1 + vat_pct / 100)``, both
    in whole pesos. The cost is read live from the data warehouse (morning inventory photo).
    """

    result = await service.price_list()
    return EmployeePriceListResponse(
        warehouse=result.warehouse,
        generated_at=result.generated_at,
        snapshot_date=result.snapshot_date,
        total=len(result.items),
        without_cost=result.without_cost,
        unknown_vat=result.unknown_vat,
        items=[EmployeePriceResponse(**asdict(item)) for item in result.items],
    )


@router.get(
    "/products/margins/template",
    summary="Margin template",
    response_class=Response,
    responses={200: {"content": {_XLSX: {}}}},
)
@inject
async def margin_template(
    with_data: bool = Query(
        default=True, description="Include every product with its current margins"
    ),
    _: Principal = _principal,
    service: CommercialMasterService = _service,
) -> Response:
    """The margin file as an ``.xlsx``: edit it and upload it back to update the margins."""

    products = await service.products_with_margins() if with_data else []
    return Response(
        content=template_workbook(products),
        media_type=_XLSX,
        headers={"Content-Disposition": 'attachment; filename="margenes-productos.xlsx"'},
    )


@router.post(
    "/products/margins",
    response_model=MarginReportResponse,
    summary="Update margins from a file",
    description=(
        "multipart/form-data with `file` (.xlsx or .csv; columns Referencia and any of "
        "Margen Lista 1, Margen Mayorista, Margen Masivo Food, Margen Market Apartadó, "
        "IMPTOLIC, Observaciones; an old «Margen Salsamentarias» column is ignored). Blank "
        "cells keep the margin, 0 clears it. An .xlsx may carry the IMPUESTOS table (Sku, "
        "Nacional, Costa, Grupo impositivo) of `Margen Proveedor-Impuestos`: the liquor tax by "
        "regional. "
        "A simulation unless `apply=true`."
    ),
)
@inject
async def update_margins(
    file: UploadFile = File(...),  # noqa: B008
    apply: bool = Query(default=False),
    sheet: str | None = Query(default=None, max_length=100),
    principal: Principal = _principal,
    service: CommercialMasterService = _service,
) -> MarginReportResponse:
    """Compare a margin file with the products and, with ``apply``, write the differences."""

    # One byte over the limit is enough for the service to reject the file.
    content = await file.read(service.max_upload_bytes + 1)
    report = await service.update_margins(
        principal, file.filename, content, apply=apply, sheet=sheet
    )
    return _report(report)


class PriceIncreaseResponse(BaseModel):
    """One cost change a supplier notified, with the product's data from the master."""

    id: UUID
    sku: str
    description: str | None
    supplier_name: str | None
    category: str | None
    is_known_product: bool
    notified_on: date | None
    system_updated_on: date | None
    increased_on: date | None
    supplier_sku: str | None
    ean: str | None
    regional: str | None
    icui_pct: Decimal | None
    ibua: Decimal | None
    old_cost_with_tax: Decimal | None
    old_cost: Decimal | None
    new_cost: Decimal | None
    new_cost_with_tax: Decimal | None
    variation_amount: Decimal | None
    variation_pct: Decimal | None
    status: Literal["subio", "bajo", "estable"] | None
    status_label: str | None
    observations: str | None
    margin_pct: Decimal | None
    price: Decimal | None
    notified: bool
    siesa_changed: bool
    # No system update date: SIESA does not have it, so the quoter compares its new cost.
    is_pending: bool
    updated_by: str
    updated_at: datetime


class PriceIncreaseCountsResponse(BaseModel):
    subio: int
    bajo: int
    estable: int
    pendientes: int


class PriceIncreasePageResponse(BaseModel):
    items: list[PriceIncreaseResponse]
    total: int
    page: int
    size: int
    # Counts of the whole filter, not the page.
    counts: PriceIncreaseCountsResponse
    # Whether the caller may upload the workbook and set the system update date.
    can_manage: bool


class PriceIncreaseIssueResponse(BaseModel):
    row_number: int
    sku: str | None
    problem: str


class PriceIncreaseReportResponse(BaseModel):
    """What an upload did, or would do."""

    applied: bool
    rows_read: int
    inserted: int
    updated: int
    unchanged: int
    duplicated: int
    unknown_skus: int
    pending: int
    issues: list[PriceIncreaseIssueResponse]
    issues_truncated: bool


class SystemDateRequest(BaseModel):
    """The day SIESA got the increase; ``null`` marks it pending again."""

    system_updated_on: date | None


def _increase(item: PriceIncrease) -> PriceIncreaseResponse:
    values = item.values
    return PriceIncreaseResponse(
        id=item.id,
        description=item.description,
        supplier_name=item.supplier_name,
        category=item.category,
        is_known_product=item.is_known_product,
        status_label=PRICE_STATUS_LABELS.get(values.status or ""),
        is_pending=item.is_pending,
        updated_by=item.updated_by,
        updated_at=item.updated_at,
        **asdict(values),
    )


Movement = Literal["subio", "bajo", "estable"]


class ReferenceAnalysisResponse(BaseModel):
    """One reference over the period: how much it moved and how its last change ended."""

    sku: str
    description: str | None
    supplier_code: str | None
    supplier_name: str | None
    category: str | None
    # Rows of the period, of them how many went up and how many down.
    changes: int
    increases: int
    decreases: int
    # Cost just before the period and at its end (without the healthy-food tax when known).
    start_cost: Decimal | None
    end_cost: Decimal | None
    # end_cost against start_cost (a fraction: 0.1025 is 10,25 %).
    accumulated_pct: Decimal | None
    # Up, down or flat by the sign of accumulated_pct.
    movement: Movement | None
    # Median days between consecutive increases; null with fewer than two.
    frequency_days: int | None
    last_increased_on: date
    last_status: Movement | None
    last_change_pct: Decimal | None
    # The last change has no system update date: the quoter compares its new cost.
    is_pending: bool


class AnalysisSummaryResponse(BaseModel):
    """The period: references with a change, increases, the typical (median) increase,
    references pending in SIESA and references by movement (all of the search)."""

    references: int
    increases: int
    typical_increase_pct: Decimal | None
    pending: int
    movements: dict[Movement, int]


class AnalysisPageResponse(BaseModel):
    items: list[ReferenceAnalysisResponse]
    total: int
    page: int
    size: int
    summary: AnalysisSummaryResponse
    # First and last day of the period; null is an open end (both for the whole history).
    since: date | None
    until: date | None


class SupplierOptionResponse(BaseModel):
    code: str
    name: str | None
    # References of the supplier in the history.
    references: int


class MonthOptionResponse(BaseModel):
    # "YYYY-MM"
    month: str
    changes: int
    references: int


class FilterOptionsResponse(BaseModel):
    """What the dropdowns of the history and the analysis offer."""

    suppliers: list[SupplierOptionResponse]
    # The latest month first, from the year 2000.
    months: list[MonthOptionResponse]


@router.get(
    "/price-increases/options",
    response_model=FilterOptionsResponse,
    summary="Suppliers and months of the price increase history",
)
@inject
async def price_increase_options(
    _: Principal = _principal,
    service: PriceIncreaseService = _increases,
) -> FilterOptionsResponse:
    """The commercial suppliers with references in the history, by name, and the months with
    increases taking effect: the supplier and month dropdowns. Suppliers are picked from
    this list, never typed."""

    result = await service.filter_options()
    return FilterOptionsResponse(
        suppliers=[SupplierOptionResponse(**asdict(item)) for item in result.suppliers],
        months=[MonthOptionResponse(**asdict(item)) for item in result.months],
    )


@router.get(
    "/price-increases/analysis",
    response_model=AnalysisPageResponse,
    summary="How much each reference moved in a period",
)
@inject
async def price_increase_analysis(
    period: Literal["1m", "12m", "24m", "all"] = Query(default="12m"),
    since: date | None = Query(default=None),  # noqa: B008
    until: date | None = Query(default=None),  # noqa: B008
    supplier: str | None = Query(default=None, max_length=40),
    movement: Movement | None = Query(default=None),  # noqa: B008
    q: str | None = Query(default=None, max_length=100),
    sort: Literal["accumulated", "increases", "frequency", "last_change", "sku"] = Query(
        default="accumulated"
    ),
    order: Literal["asc", "desc"] = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=PRICE_INCREASES_MAX_PAGE),
    _: Principal = _principal,
    service: PriceIncreaseService = _increases,
) -> AnalysisPageResponse:
    """Each reference with a change in the period, dated by when the increase takes effect:
    its cost at the end against just before the period, how many times it went up, how
    often, and its last change.

    The period is the last year by default, ``1m``, ``24m`` or ``all``; ``since`` and
    ``until`` (both days included, either may be left open) replace it with a range or a
    calendar month. ``q`` matches the reference (prefix) or the description; ``supplier`` is
    a commercial supplier code from ``/price-increases/options``. ``movement`` keeps the
    references that went up, down or stayed; ``summary`` covers the search before that
    filter."""

    result = await service.analysis(
        period=period,
        since=since,
        until=until,
        supplier=supplier,
        q=q,
        movement=movement,
        sort=sort,
        descending=order == "desc",
        page=page,
        size=size,
    )
    return AnalysisPageResponse(
        items=[ReferenceAnalysisResponse(**asdict(item)) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
        summary=AnalysisSummaryResponse(**asdict(result.summary)),
        since=result.since,
        until=result.until,
    )


@router.get(
    "/price-increases",
    response_model=PriceIncreasePageResponse,
    summary="Price increase history",
)
@inject
async def list_price_increases(
    q: str | None = Query(default=None, max_length=100),
    status: Literal["subio", "bajo", "estable"] | None = Query(default=None),
    pending: bool | None = Query(default=None),
    sku: str | None = Query(default=None, max_length=40),
    supplier: str | None = Query(default=None, max_length=40),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=PRICE_INCREASES_MAX_PAGE),
    principal: Principal = _principal,
    service: PriceIncreaseService = _increases,
) -> PriceIncreasePageResponse:
    """The history, the latest increase first. ``q`` matches the reference (prefix) or the
    description; ``supplier`` is a commercial supplier code from ``/price-increases/options``;
    ``pending=true`` keeps the rows SIESA does not have yet."""

    result = await service.page(
        PriceIncreaseFilters(q=q, status=status, pending=pending, sku=sku, supplier=supplier),
        page,
        size,
    )
    return PriceIncreasePageResponse(
        items=[_increase(item) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
        counts=PriceIncreaseCountsResponse(**result.counts),
        can_manage=await service.can_manage(principal),
    )


@router.post(
    "/price-increases/upload",
    response_model=PriceIncreaseReportResponse,
    summary="Upsert the price increase workbook",
    description=(
        "multipart/form-data with `file` (.xlsx, the `Acumulado Alza Precios` layout). Rows "
        "are upserted by Referencia, Fecha de Incremento and Fecha de notificación; the "
        "product's own columns are ignored. A simulation unless `apply=true`. Masters "
        "managers only."
    ),
)
@inject
async def upload_price_increases(
    file: UploadFile = File(...),  # noqa: B008
    apply: bool = Query(default=False),
    principal: Principal = _principal,
    service: PriceIncreaseService = _increases,
) -> PriceIncreaseReportResponse:
    """Compare the workbook with the history and, with ``apply``, write the differences."""

    content = await file.read(service.max_upload_bytes + 1)
    report = await service.upload(principal, file.filename, content, apply=apply)
    return PriceIncreaseReportResponse(
        **{
            name: getattr(report, name)
            for name in PriceIncreaseReportResponse.model_fields
            if name != "issues"
        },
        issues=[PriceIncreaseIssueResponse(**asdict(issue)) for issue in report.issues],
    )


@router.patch(
    "/price-increases/{increase_id}",
    response_model=PriceIncreaseResponse,
    summary="Set the day SIESA got an increase",
)
@inject
async def set_price_increase_system_date(
    increase_id: UUID,
    body: SystemDateRequest,
    principal: Principal = _principal,
    service: PriceIncreaseService = _increases,
) -> PriceIncreaseResponse:
    """``null`` marks it pending again: the quoter then compares its new cost. Masters
    managers only."""

    return _increase(await service.set_system_date(principal, increase_id, body.system_updated_on))


@router.get("/products/{sku}", response_model=ProductResponse, summary="Get a product")
@inject
async def get_product(
    sku: str,
    _: Principal = _principal,
    service: CommercialMasterService = _service,
) -> ProductResponse:
    """One product with its margins and liquor tax."""

    return _product(await service.get_product(sku))


@router.patch("/products/{sku}", response_model=ProductResponse, summary="Update a product")
@inject
async def update_product(
    sku: str,
    body: ProductUpdate,
    principal: Principal = _principal,
    service: CommercialMasterService = _service,
) -> ProductResponse:
    """Change only the fields sent, margins included. Managers only."""

    changes: dict[str, Any] = {
        name: getattr(body, name) for name in body.model_fields_set - _IGNORED_FIELDS
    }
    return _product(await service.update_product(principal, sku, changes))


@router.put(
    "/products/{sku}/margins",
    response_model=ProductResponse,
    summary="Set the margins of one product",
)
@inject
async def set_product_margins(
    sku: str,
    body: ProductMarginsRequest,
    principal: Principal = _principal,
    service: CommercialMasterService = _service,
) -> ProductResponse:
    """Fija los cuatro márgenes de un producto, sus notas y su impuesto licor, sin archivo.

    Solo gestores. 422 si falta un margen o si el producto paga impuesto licor y no llega.
    """

    liquor = body.liquor_tax
    product = await service.set_product_margins(
        principal,
        sku,
        {name: getattr(body, name) for name in MARGIN_FIELDS},
        body.notes,
        LiquorTax(liquor.national, liquor.coast, liquor.group) if liquor else None,
        employee_margin=body.margin_employee,
    )
    return _product(product)


__all__ = ["router"]
