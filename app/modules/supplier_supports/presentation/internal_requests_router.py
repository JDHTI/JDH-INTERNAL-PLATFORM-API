"""Internal support requests: asked from a quotation, decided by BI or a reviewer.

Whoever quotes asks from a customer quotation; BI or a designated reviewer decides. Margins are
fractions (``"0.16"``); dates ``YYYY-MM-DD``.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.commercial_masters.domain.margins import MARGIN_LABELS
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.supplier_supports.application.internal_requests import (
    MAX_PAGE_SIZE,
    InternalRequestService,
)
from app.modules.supplier_supports.domain.internal_requests import MAX_ITEMS, InternalRequest

router = APIRouter(
    dependencies=[
        Depends(
            require_permission(
                "cotizador", "apoyos", "aprobacion-apoyos-internos", "solicitudes-apoyos"
            )
        )
    ]
)
_service = Depends(Provide[ApplicationContainer.v1_container.internal_support_request_service])
_principal = Depends(get_current_principal)

Status = Literal["pendiente", "aprobada", "rechazada"]
Decision = Literal["persistente", "unica_vez"]


class RequestItemBody(BaseModel):
    sku: str = Field(min_length=1, max_length=50)
    requested_margin_pct: Decimal = Field(ge=0, lt=1)


class CreateRequestBody(BaseModel):
    """Ask a supported margin for SKUs of one supplier of the quotation's customer."""

    version: int = Field(ge=1)
    supplier_code: str = Field(min_length=1, max_length=20)
    # The order's date; empty = today (Bogotá).
    requested_for: date | None = None
    agreed_with: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=1000)
    items: list[RequestItemBody] = Field(min_length=1, max_length=MAX_ITEMS)


class DirectRequestBody(BaseModel):
    """Ask an authorized margin by customer and SKU, from the requests panel (no quotation).
    Every SKU must be of one supplier and each margin lower than its current one."""

    customer_nit: str = Field(min_length=1, max_length=30)
    # The order's date; empty = today (Bogotá).
    requested_for: date | None = None
    agreed_with: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=1000)
    items: list[RequestItemBody] = Field(min_length=1, max_length=MAX_ITEMS)


class TargetMarginResponse(BaseModel):
    """The current margin of a SKU for a customer: the product's optimal margin on the
    customer's list. A request must ask less."""

    sku: str
    description: str | None
    supplier_code: str | None
    margin_field: str
    margin_label: str
    # Null when the product has no margin on that list (it cannot be requested).
    margin_pct: Decimal | None


class TargetMarginsResponse(BaseModel):
    customer_nit: str
    customer_name: str | None
    items: list[TargetMarginResponse]


class ApproveItemBody(BaseModel):
    sku: str = Field(min_length=1, max_length=50)
    approved_margin_pct: Decimal = Field(ge=0, lt=1)


class ApproveBody(BaseModel):
    """``unica_vez``: no dates (the order's date is used) and a note is required. The SKUs
    left out are not approved."""

    version: int = Field(ge=1)
    decision: Decision
    valid_from: date | None = None
    valid_to: date | None = None
    note: str | None = Field(default=None, max_length=1000)
    items: list[ApproveItemBody] = Field(min_length=1, max_length=MAX_ITEMS)


class RejectBody(BaseModel):
    version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


class RequestItemResponse(BaseModel):
    sku: str
    description: str | None
    unit: str | None
    cost: Decimal | None
    quoted_price: Decimal | None
    # Margin the quoted price leaves (with the supports in force).
    margin_pct: Decimal | None
    # Minimum of the product or the rule on that line.
    min_margin_pct: Decimal | None
    # The product's current (optimal) margin for the customer when it was asked.
    target_margin_pct: Decimal | None = None
    requested_margin_pct: Decimal
    # Null when that SKU was not approved.
    approved_margin_pct: Decimal | None
    # The support created for it.
    support_id: UUID | None


class InternalRequestResponse(BaseModel):
    id: UUID
    number: int
    status: Status
    decision: Decision | None
    quotation_id: UUID | None
    quotation_label: str | None
    customer_nit: str
    customer_name: str | None
    supplier_code: str
    supplier_name: str | None
    requested_for: date
    agreed_with: str
    reason: str
    requested_by: str
    created_at: datetime
    version: int
    valid_from: date | None
    valid_to: date | None
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    items: list[RequestItemResponse]


class InternalRequestPageResponse(BaseModel):
    items: list[InternalRequestResponse]
    total: int
    page: int
    size: int


def _response(request: InternalRequest) -> InternalRequestResponse:
    return InternalRequestResponse(
        id=request.id,
        number=request.number,
        status=request.status,  # type: ignore[arg-type]
        decision=request.decision,  # type: ignore[arg-type]
        quotation_id=request.quotation_id,
        quotation_label=request.quotation_label,
        customer_nit=request.customer_nit,
        customer_name=request.customer_name,
        supplier_code=request.supplier_code,
        supplier_name=request.supplier_name,
        requested_for=request.requested_for,
        agreed_with=request.agreed_with,
        reason=request.reason,
        requested_by=request.requested_by,
        created_at=request.created_at,
        version=request.version,
        valid_from=request.valid_from,
        valid_to=request.valid_to,
        decided_by=request.decided_by,
        decided_at=request.decided_at,
        decision_reason=request.decision_reason,
        items=[
            RequestItemResponse(
                sku=item.sku,
                description=item.description,
                unit=item.unit,
                cost=item.cost,
                quoted_price=item.quoted_price,
                margin_pct=item.margin_pct,
                min_margin_pct=item.min_margin_pct,
                target_margin_pct=item.target_margin_pct,
                requested_margin_pct=item.requested_margin_pct,
                approved_margin_pct=item.approved_margin_pct,
                support_id=item.support_id,
            )
            for item in request.items
        ],
    )


@router.post(
    "/quotations/{quotation_id}/support-requests",
    response_model=InternalRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ask an internal support from a customer quotation",
)
@inject
async def create_request(
    quotation_id: UUID,
    body: CreateRequestBody,
    principal: Principal = _principal,
    service: InternalRequestService = _service,
) -> InternalRequestResponse:
    """One customer and SKUs of one supplier of the quotation, with the margin asked.
    409 when the quotation changed or a SKU is already pending for it."""

    return _response(
        await service.create(
            principal,
            quotation_id,
            version=body.version,
            supplier_code=body.supplier_code,
            requested_for=body.requested_for,
            agreed_with=body.agreed_with,
            reason=body.reason,
            items=[(item.sku, item.requested_margin_pct) for item in body.items],
        )
    )


@router.get(
    "/internal-support-requests/target-margins",
    response_model=TargetMarginsResponse,
    summary="Current margin of SKUs for a customer, to ask a lower one",
)
@inject
async def target_margins(
    customer_nit: str = Query(min_length=1, max_length=30),
    sku: list[str] = Query(min_length=1, max_length=MAX_ITEMS),  # noqa: B008
    principal: Principal = _principal,
    service: InternalRequestService = _service,
) -> TargetMarginsResponse:
    """The product's optimal margin on the customer's margin list (Market Apartadó, Masivo
    food, Mayoristas or Food service), the one an authorized margin must be under. Unknown
    SKUs are left out; 422 for an unknown customer."""

    nit, name, found = await service.targets(principal, customer_nit, sku)
    return TargetMarginsResponse(
        customer_nit=nit,
        customer_name=name,
        items=[
            TargetMarginResponse(
                sku=item.sku,
                description=item.description,
                supplier_code=item.commercial_supplier_code,
                margin_field=item.margin_field,
                margin_label=MARGIN_LABELS.get(item.margin_field, item.margin_field),
                margin_pct=item.margin_pct,
            )
            for item in found
        ],
    )


@router.post(
    "/internal-support-requests",
    response_model=InternalRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ask an authorized margin by customer and SKU (requests panel)",
)
@inject
async def create_direct_request(
    body: DirectRequestBody,
    principal: Principal = _principal,
    service: InternalRequestService = _service,
) -> InternalRequestResponse:
    """No quotation: one customer and SKUs of one supplier, each with a margin lower than its
    current one (422 otherwise). Approving creates the margin exception, as from a quotation.
    409 when a SKU is already pending for that customer."""

    return _response(
        await service.create_direct(
            principal,
            customer_nit=body.customer_nit,
            requested_for=body.requested_for,
            agreed_with=body.agreed_with,
            reason=body.reason,
            items=[(item.sku, item.requested_margin_pct) for item in body.items],
        )
    )


@router.get(
    "/internal-support-requests",
    response_model=InternalRequestPageResponse,
    summary="Internal support requests",
)
@inject
async def list_requests(
    mine: bool = Query(default=False, description="Only the caller's own requests"),
    status_: Annotated[Status | None, Query(alias="status")] = None,
    q: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    principal: Principal = _principal,
    service: InternalRequestService = _service,
) -> InternalRequestPageResponse:
    """Newest first. Without ``mine`` only reviewers may list (every request)."""

    items, total = await service.search(
        principal, mine=mine, status=status_, q=q, page=page, size=size
    )
    return InternalRequestPageResponse(
        items=[_response(item) for item in items], total=total, page=page, size=size
    )


@router.get(
    "/internal-support-requests/{request_id}",
    response_model=InternalRequestResponse,
    summary="One internal support request",
)
@inject
async def get_request(
    request_id: UUID,
    principal: Principal = _principal,
    service: InternalRequestService = _service,
) -> InternalRequestResponse:
    """Its author or a reviewer."""

    return _response(await service.get(principal, request_id))


@router.post(
    "/internal-support-requests/{request_id}/approve",
    response_model=InternalRequestResponse,
    summary="Approve an internal support request",
)
@inject
async def approve_request(
    request_id: UUID,
    body: ApproveBody,
    principal: Principal = _principal,
    service: InternalRequestService = _service,
) -> InternalRequestResponse:
    """For good (with a validity) or once (the order's date); creates one support
    ``excepcion_margen`` per approved SKU. 409 when it changed; 403 without permission."""

    return _response(
        await service.approve(
            principal,
            request_id,
            version=body.version,
            decision=body.decision,
            valid_from=body.valid_from,
            valid_to=body.valid_to,
            note=body.note,
            items=[(item.sku, item.approved_margin_pct) for item in body.items],
        )
    )


@router.post(
    "/internal-support-requests/{request_id}/reject",
    response_model=InternalRequestResponse,
    summary="Reject an internal support request",
)
@inject
async def reject_request(
    request_id: UUID,
    body: RejectBody,
    principal: Principal = _principal,
    service: InternalRequestService = _service,
) -> InternalRequestResponse:
    """With a reason. 409 when it changed; 403 without permission."""

    return _response(
        await service.reject(principal, request_id, version=body.version, reason=body.reason)
    )


__all__ = ["router"]
