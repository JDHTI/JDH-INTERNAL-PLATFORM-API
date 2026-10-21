"""Endpoints of the commercial masters (suppliers, customers, products)."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.modules.commercial_masters.application.service import (
    MAX_PAGE_SIZE,
    CommercialMasterService,
)
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal

router = APIRouter()
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
    list_margin_pct: Decimal | None
    list_margin_notes: str | None
    nits: list[CommercialSupplierNitResponse]


class CommercialSupplierUpdate(BaseModel):
    """Editable data of a commercial supplier; omitted fields are untouched."""

    list_margin_pct: Decimal | None = Field(default=None, ge=0, lt=1)
    list_margin_notes: str | None = Field(default=None, max_length=1000)
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
    pack_factor: Decimal | None
    inventory_unit: str | None
    main_barcode: str | None


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
    """Set the list margin and confirm legal NITs. Managers only."""

    supplier = await service.update_commercial_supplier(
        principal,
        code,
        fields=frozenset(body.model_fields_set),
        list_margin_pct=body.list_margin_pct,
        list_margin_notes=body.list_margin_notes,
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
    """Search legal suppliers (NIT and branch) by name or NIT."""

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
    """Search customers by name or NIT, channel, business unit and group."""

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
    page: int = _PAGE,
    size: int = _SIZE,
    _: Principal = _principal,
    service: CommercialMasterService = _service,
) -> PageResponse[ProductResponse]:
    """Search products by SKU or description, brand, category and commercial supplier."""

    result = await service.list_products(
        q, brand, category, commercial_supplier_code, is_active, page, size
    )
    return PageResponse(
        items=[ProductResponse.model_validate(item, from_attributes=True) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


__all__ = ["router"]
