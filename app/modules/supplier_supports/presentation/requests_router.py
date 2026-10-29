"""Commercial requests and BI decisions; authorization is enforced server-side."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.app_container import ApplicationContainer
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.supplier_supports.application.requests import SupportRequestService
from app.modules.supplier_supports.application.service import SupplierSupportService
from app.modules.supplier_supports.presentation.router import SupportResponse, _response

router = APIRouter()
_principal = Depends(get_current_principal)
_service = Depends(Provide[ApplicationContainer.v1_container.supplier_support_request_service])
_supports = Depends(Provide[ApplicationContainer.v1_container.supplier_support_service])


class VersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class RequestBody(VersionBody):
    value: Decimal = Field(max_digits=18, decimal_places=6, allow_inf_nan=False)
    reason: str = Field(min_length=3, max_length=2000)


class ApproveBody(VersionBody):
    support_version: int = Field(ge=1)


class ReasonBody(VersionBody):
    reason: str = Field(min_length=3, max_length=2000)


class EventResponse(BaseModel):
    id: UUID
    support_id: UUID
    request_id: UUID | None
    event_type: str
    actor: str
    created_at: datetime
    reason: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None


class RequestResponse(BaseModel):
    id: UUID
    support_id: UUID
    mode: Literal["porcentaje", "costo_neto"]
    value: Decimal
    reason: str
    requested_by_id: UUID
    requested_by: str
    created_at: datetime
    status: Literal["pendiente", "aprobada", "rechazada"]
    version: int
    support_version: int
    snapshot: dict[str, Any]
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None


class RequestDetailResponse(RequestResponse):
    support: SupportResponse
    events: list[EventResponse]


class RequestPageResponse(BaseModel):
    items: list[RequestResponse]
    total: int
    page: int
    size: int


@router.post(
    "/supplier-supports/{support_id}/requests", status_code=201, response_model=RequestResponse
)
@inject
async def create_request(
    support_id: UUID,
    body: RequestBody,
    principal: Principal = _principal,
    service: SupportRequestService = _service,
):
    return await service.create(principal, support_id, body.version, body.value, body.reason)


@router.get("/supplier-support-requests", response_model=RequestPageResponse)
@inject
async def search_requests(
    status: Literal["pendiente", "aprobada", "rechazada"] | None = None,
    q: str | None = Query(default=None, max_length=100),
    mine: bool = False,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    principal: Principal = _principal,
    service: SupportRequestService = _service,
):
    return await service.search(principal, status, q, page, size, mine)


@router.get("/supplier-support-requests/{request_id}", response_model=RequestDetailResponse)
@inject
async def get_request(
    request_id: UUID,
    mine: bool = False,
    principal: Principal = _principal,
    service: SupportRequestService = _service,
    supports: SupplierSupportService = _supports,
):
    result = await service.get(principal, request_id, mine)
    return {**result, "support": _response(await supports.get(result["support_id"]))}


@router.post("/supplier-support-requests/{request_id}/approve", response_model=RequestResponse)
@inject
async def approve_request(
    request_id: UUID,
    body: ApproveBody,
    principal: Principal = _principal,
    service: SupportRequestService = _service,
):
    return await service.decide(principal, request_id, body.version, body.support_version, True)


@router.post("/supplier-support-requests/{request_id}/reject", response_model=RequestResponse)
@inject
async def reject_request(
    request_id: UUID,
    body: ReasonBody,
    principal: Principal = _principal,
    service: SupportRequestService = _service,
):
    return await service.decide(principal, request_id, body.version, None, False, body.reason)


@router.delete("/supplier-supports/{support_id}/jdh-support", response_model=SupportResponse)
@inject
async def cancel_jdh(
    support_id: UUID,
    body: ReasonBody,
    principal: Principal = _principal,
    service: SupportRequestService = _service,
    supports: SupplierSupportService = _supports,
):
    await service.cancel(principal, support_id, body.version, body.reason)
    return _response(await supports.get(support_id))


@router.get("/supplier-supports/{support_id}/jdh-history", response_model=list[EventResponse])
@inject
async def jdh_history(
    support_id: UUID, principal: Principal = _principal, service: SupportRequestService = _service
):
    return await service.history(principal, support_id)
