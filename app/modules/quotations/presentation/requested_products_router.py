"""Endpoints of the requested products registry (``/requested-products``).

Its own module permission (``productos-solicitados``), so an administrator switches it on
per area. Quoters and validators of the quoter read it anyway; only validators (and
platform admins) resolve an entry. Local lines of the quotations keep feeding it.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.quotations.application.service import MAX_PAGE_SIZE, QuotationService
from app.modules.quotations.domain.entities import QuotationRef, RequestedProduct
from app.modules.quotations.domain.vocabulary import REQUESTED_STATUS_LABELS, STATUS_LABELS
from app.modules.quotations.presentation.router import RequestedStatus, Status

MODULE = "productos-solicitados"
router = APIRouter()
# The registry's own module, or the quoter: quoters and validators read it too.
_READ_GATE = Depends(require_permission(MODULE, "cotizador"))
_service = Depends(Provide[ApplicationContainer.v1_container.quotation_service])
_principal = Depends(get_current_principal)


class RegistryPermissionsResponse(BaseModel):
    can_read: bool
    # Mark entries created, discarded or requested (validators and platform admins).
    can_resolve: bool


class RequestedQuotationResponse(BaseModel):
    id: UUID
    customer_name: str | None
    customer_nit: str
    status: Status
    status_label: str
    created_by: str


class RequestedProductResponse(BaseModel):
    """A product an advisor quoted that SIESA does not have."""

    id: UUID
    description: str
    supplier_name: str | None
    estimated_price: Decimal | None
    vat_pct: Decimal | None
    status: RequestedStatus
    status_label: str
    siesa_sku: str | None
    note: str | None
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime
    # Only the quotations the caller can open.
    quotations: list[RequestedQuotationResponse]


class RequestedProductPageResponse(BaseModel):
    items: list[RequestedProductResponse]
    total: int
    page: int
    size: int


class RequestedProductUpdate(BaseModel):
    """BI's decision on a requested product."""

    status: RequestedStatus
    siesa_sku: str | None = Field(default=None, max_length=60)
    note: str | None = Field(default=None, max_length=4000)


def _requested(item: RequestedProduct, refs: list[QuotationRef]) -> RequestedProductResponse:
    return RequestedProductResponse(
        id=item.id,
        description=item.description,
        supplier_name=item.supplier_name,
        estimated_price=item.estimated_price,
        vat_pct=item.vat_pct,
        status=item.status,  # type: ignore[arg-type]
        status_label=REQUESTED_STATUS_LABELS.get(item.status, item.status),
        siesa_sku=item.siesa_sku,
        note=item.note,
        created_by=item.created_by,
        created_at=item.created_at,
        updated_by=item.updated_by,
        updated_at=item.updated_at,
        quotations=[
            RequestedQuotationResponse(
                id=ref.id,
                customer_name=ref.customer_name,
                customer_nit=ref.customer_nit,
                status=ref.status,  # type: ignore[arg-type]
                status_label=STATUS_LABELS.get(ref.status, ref.status),
                created_by=ref.created_by,
            )
            for ref in refs
        ],
    )


@router.get(
    "/permissions",
    response_model=RegistryPermissionsResponse,
    summary="What the caller may do in the registry",
)
@inject
async def registry_permissions(
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> RegistryPermissionsResponse:
    """Open to every user, to decide what the screen shows."""

    can_read, can_resolve = await service.registry_permissions(principal)
    return RegistryPermissionsResponse(can_read=can_read, can_resolve=can_resolve)


@router.get(
    "",
    dependencies=[_READ_GATE],
    response_model=RequestedProductPageResponse,
    summary="Products advisors asked for that SIESA does not have",
)
@inject
async def list_requested_products(
    status_filter: RequestedStatus | None = Query(default=None, alias="status"),  # noqa: B008
    q: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> RequestedProductPageResponse:
    """``solicitado`` first, then the newest. ``q`` matches the description or the supplier;
    each item lists only the quotations the caller can open."""

    result, refs = await service.requested_products(
        principal, status=status_filter, q=q, page=page, size=size
    )
    return RequestedProductPageResponse(
        items=[_requested(item, refs.get(item.id, [])) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.patch(
    "/{product_id}",
    dependencies=[_READ_GATE],
    response_model=RequestedProductResponse,
    summary="Mark a requested product created, discarded or requested",
)
@inject
async def update_requested_product(
    product_id: UUID,
    body: RequestedProductUpdate,
    principal: Principal = _principal,
    service: QuotationService = _service,
) -> RequestedProductResponse:
    """Validators and platform admins. ``creado`` needs a SIESA reference that exists and is
    active; back to ``solicitado`` clears it. ``note`` changes only when sent."""

    product, refs = await service.update_requested_product(
        principal,
        product_id,
        status=body.status,
        siesa_sku=body.siesa_sku,
        note=body.note,
        set_note="note" in body.model_fields_set,
    )
    return _requested(product, refs)
