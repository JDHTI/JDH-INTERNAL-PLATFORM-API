"""Section of the document requests answered automatically (PQRSF managers)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.pqrsf.application.document_delivery import DocumentDeliveryService
from app.modules.pqrsf.domain.certificates import DeliveryFilters, DocumentDelivery

router = APIRouter(
    prefix="/pqrsf/document-deliveries", dependencies=[Depends(require_permission("pqrsf"))]
)
_service = Depends(Provide[ApplicationContainer.v1_container.pqrsf_delivery_service])
_principal = Depends(get_current_principal)


class DeliveredDocumentResponse(BaseModel):
    """A certificate that was sent."""

    document_type_id: UUID | None
    name: str
    filename: str
    size_bytes: int


class DeliverySiteResponse(BaseModel):
    """Site the request was made for."""

    id: UUID
    code: str
    name: str


class PersonResponse(BaseModel):
    """Who opened the delivery first."""

    id: UUID
    display_name: str | None
    email: str | None


class DeliveryResponse(BaseModel):
    """A document request answered with certificates."""

    id: UUID
    full_name: str
    business_name: str | None
    nit: str | None
    email: str
    site: DeliverySiteResponse | None
    documents: list[DeliveredDocumentResponse]
    # What the same request asked for that goes through a case, and that case.
    manual_documents: list[str]
    case_code: str | None
    status: Literal["pending", "sent", "failed"]
    sent_at: datetime | None
    error: str | None
    read_at: datetime | None
    created_at: datetime


class DeliveryDetailResponse(DeliveryResponse):
    """A delivery in full."""

    read_by: PersonResponse | None


class DeliveryPageResponse(BaseModel):
    """One page of deliveries."""

    items: list[DeliveryResponse]
    total: int
    page: int
    size: int


class DeliverySummaryResponse(BaseModel):
    """Totals for the menu badge."""

    total: int
    unread: int


def _delivery(value: DocumentDelivery) -> dict:
    return {
        "id": value.id,
        "full_name": value.full_name,
        "business_name": value.business_name,
        "nit": value.nit,
        "email": value.email,
        "site": (
            DeliverySiteResponse(id=value.site.id, code=value.site.code, name=value.site.name)
            if value.site
            else None
        ),
        "documents": [
            DeliveredDocumentResponse(
                document_type_id=i.document_type_id,
                name=i.name,
                filename=i.filename,
                size_bytes=i.size_bytes,
            )
            for i in value.items
        ],
        "manual_documents": value.manual_items,
        "case_code": value.case_code,
        "status": value.status,
        "sent_at": value.sent_at,
        "error": value.error,
        "read_at": value.read_at,
        "created_at": value.created_at,
    }


@router.get(
    "/summary", response_model=DeliverySummaryResponse, summary="Document deliveries totals"
)
@inject
async def summary(
    principal: Principal = _principal, service: DocumentDeliveryService = _service
) -> DeliverySummaryResponse:
    """Totals and unread, for the menu badge (PQRSF managers)."""

    value = await service.summary(principal)
    return DeliverySummaryResponse(total=value.total, unread=value.unread)


@router.get("", response_model=DeliveryPageResponse, summary="Requests answered with certificates")
@inject
async def list_deliveries(
    q: str | None = Query(default=None, max_length=100),  # noqa: B008
    unread: bool = False,
    date_from: date | None = Query(default=None, alias="from"),  # noqa: B008
    date_to: date | None = Query(default=None, alias="to"),  # noqa: B008
    page: int = Query(default=1, ge=1),  # noqa: B008
    size: int = Query(default=10, ge=1, le=100),  # noqa: B008
    principal: Principal = _principal,
    service: DocumentDeliveryService = _service,
) -> DeliveryPageResponse:
    """Newest first, 10 at a time by default."""

    result = await service.list_deliveries(
        principal,
        DeliveryFilters(query=q, unread_only=unread, created_from=date_from, created_to=date_to),
        page,
        size,
    )
    return DeliveryPageResponse(
        items=[DeliveryResponse(**_delivery(item)) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.get("/{delivery_id}", response_model=DeliveryDetailResponse, summary="Read a delivery")
@inject
async def get_delivery(
    delivery_id: UUID,
    principal: Principal = _principal,
    service: DocumentDeliveryService = _service,
) -> DeliveryDetailResponse:
    """The delivery in full; the first opening marks it as read."""

    value = await service.open(delivery_id, principal)
    reader = await service.reader(value)
    return DeliveryDetailResponse(
        **_delivery(value),
        read_by=(
            PersonResponse(id=reader.id, display_name=reader.display_name, email=reader.email)
            if reader
            else None
        ),
    )


@router.post(
    "/{delivery_id}/retry", response_model=DeliveryResponse, summary="Send a failed delivery again"
)
@inject
async def retry_delivery(
    delivery_id: UUID,
    principal: Principal = _principal,
    service: DocumentDeliveryService = _service,
) -> DeliveryResponse:
    """Only for a delivery whose emails gave up."""

    return DeliveryResponse(**_delivery(await service.retry(delivery_id, principal)))


__all__ = ["router"]
