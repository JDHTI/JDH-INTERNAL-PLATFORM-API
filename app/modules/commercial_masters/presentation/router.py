"""Endpoints of the commercial masters (suppliers, customers, products)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.commercial_masters.application.service import (
    MAX_PAGE_SIZE,
    CommercialMasterService,
)
from app.modules.commercial_masters.domain.entities import Product
from app.modules.commercial_masters.domain.margins import MarginReport
from app.modules.commercial_masters.infrastructure.margins_file import template_workbook
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal

router = APIRouter(
    dependencies=[
        Depends(
            require_permission(
                "maestros", "apoyos", "casos-especiales", "cotizador", "validador-pedidos"
            )
        )
    ]
)
_service = Depends(Provide[ApplicationContainer.v1_container.commercial_master_service])
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
    margin_list: Decimal | None
    margin_wholesale: Decimal | None
    margin_mass_food: Decimal | None
    margin_market: Decimal | None
    margin_salsamentarias: Decimal | None
    margin_notes: str | None
    margin_source: str | None
    margin_updated_by: str | None
    margin_updated_at: datetime | None
    has_margin: bool
    liquor_tax: Decimal | None
    liquor_tax_coast: Decimal | None
    liquor_tax_group: str | None


class ProductUpdate(BaseModel):
    """Fields of a product to change; omitted ones stay, ``null`` clears a margin or a text.

    Margins are fractions (0.18 = 18 %). Warehouse fields (state, supplier) are not editable
    and the others are overwritten by the next sync; margins and the liquor tax are not.
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
    margin_salsamentarias: Decimal | None = Field(default=None, gt=0, lt=1)
    margin_notes: str | None = Field(default=None, max_length=1000)
    liquor_tax: Decimal | None = Field(default=None, ge=0)
    liquor_tax_coast: Decimal | None = Field(default=None, ge=0)
    liquor_tax_group: str | None = Field(default=None, max_length=20)


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
        margin_list=margins.list,
        margin_wholesale=margins.wholesale,
        margin_mass_food=margins.mass_food,
        margin_market=margins.market,
        margin_salsamentarias=margins.salsamentarias,
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
    page: int = _PAGE,
    size: int = _SIZE,
    _: Principal = _principal,
    service: CommercialMasterService = _service,
) -> PageResponse[ProductResponse]:
    """Search products by SKU, description, brand, category, supplier, active flag and margin.

    Active products come first, then the inactive ones.
    """

    result = await service.list_products(
        q, brand, category, commercial_supplier_code, is_active, page, size, has_margin
    )
    return PageResponse(
        items=[_product(item) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
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
        "Margen Salsamentarias, IMPTOLIC, Observaciones). Blank cells keep the margin, 0 "
        "clears it. An .xlsx may carry the IMPUESTOS table (Sku, Nacional, Costa, Grupo "
        "impositivo) of `Margen Proveedor-Impuestos`: the liquor tax by regional. "
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

    changes: dict[str, Any] = {name: getattr(body, name) for name in body.model_fields_set}
    return _product(await service.update_product(principal, sku, changes))


__all__ = ["router"]
