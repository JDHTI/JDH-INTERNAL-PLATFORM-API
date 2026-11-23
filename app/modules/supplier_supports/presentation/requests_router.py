"""Commercial requests and BI decisions; authorization is enforced server-side."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.supplier_supports.application.requests import SupportRequestService
from app.modules.supplier_supports.application.service import SupplierSupportService
from app.modules.supplier_supports.presentation.router import SupportResponse, _response

router = APIRouter(dependencies=[Depends(require_permission("solicitudes-apoyos"))])
_principal = Depends(get_current_principal)
_service = Depends(Provide[ApplicationContainer.v1_container.supplier_support_request_service])
_supports = Depends(Provide[ApplicationContainer.v1_container.supplier_support_service])


class VersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class RequestBody(VersionBody):
    # Rules v2: the authorized minimum margin JDH is asked for, as a fraction (0.12 = 12 %).
    # On a % Margen Garantizado it is the supplier's minimum margin instead (2026-11-23).
    value: Decimal = Field(
        max_digits=18,
        decimal_places=6,
        allow_inf_nan=False,
        description=(
            "Excepción margen mínimo autorizado JDH, 0 ≤ valor < 1; en un % Margen "
            "Garantizado, el margen mínimo del proveedor, -1 < valor < margen vigente"
        ),
    )
    reason: str = Field(min_length=3, max_length=2000)


class BatchRequestItem(RequestBody):
    support_id: UUID


class BatchRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[BatchRequestItem] = Field(min_length=1, max_length=100)


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
    supplier_name: str | None = None
    supplier_code: str | None = None
    # margen_minimo since rules v2 (JDH); margen_proveedor on a % Margen Garantizado (the
    # supplier's exception, 2026-11-23); porcentaje / costo_neto are the earlier requests.
    mode: Literal["margen_minimo", "margen_proveedor", "porcentaje", "costo_neto"]
    mode_label: str = ""
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

    @model_validator(mode="after")
    def _label(self) -> "RequestResponse":
        if not self.mode_label:
            if self.mode == "margen_proveedor":
                supplier = self.supplier_name or self.supplier_code or "proveedor"
                self.mode_label = f"Solicitud de margen proveedor {supplier}"
            else:
                self.mode_label = _MODE_LABELS.get(self.mode, self.mode)
        return self


_MODE_LABELS = {
    "margen_minimo": "Excepción margen mínimo autorizado JDH",
    "porcentaje": "% JDH",
    "costo_neto": "Costo pactado JDH",
}


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


@router.post(
    "/supplier-supports/requests/batch", status_code=201, response_model=list[RequestResponse]
)
@inject
async def create_requests(
    body: BatchRequestBody,
    principal: Principal = _principal,
    service: SupportRequestService = _service,
):
    return await service.create_many(
        principal, [(item.support_id, item.version, item.value, item.reason) for item in body.items]
    )


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
