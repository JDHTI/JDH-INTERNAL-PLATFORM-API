"""Endpoints of the retained orders validator."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from pydantic import BaseModel

from app.app_container import ApplicationContainer
from app.core.rate_limit import rate_limit
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.order_validation.application.service import MAX_PAGE_SIZE, OrderValidationService
from app.modules.order_validation.domain.entities import (
    AgreementCheck,
    Batch,
    OrderView,
    StoredLine,
)
from app.modules.order_validation.domain.vocabulary import REASON_LABELS, STATUS_LABELS

router = APIRouter(dependencies=[Depends(rate_limit("medium"))])
_service = Depends(Provide[ApplicationContainer.v1_container.order_validation_service])
_principal = Depends(get_current_principal)

LineStatus = Literal["listo_para_liberar", "pendiente_validacion"]
Reason = Literal[
    "cumple_con_apoyo",
    "cumple_margen_lista",
    "acuerdo_en_revision",
    "no_cumple_acuerdo",
    "acuerdo_no_evaluable",
    "acuerdo_vencido",
    "sin_acuerdo",
    "datos_incompletos",
]


class BatchResponse(BaseModel):
    """An uploaded file and the counts of its latest validation."""

    id: UUID
    file_name: str
    uploaded_by: str
    uploaded_at: datetime
    validated_by: str
    validated_at: datetime
    line_count: int
    order_count: int
    ready_line_count: int
    pending_line_count: int
    ready_order_count: int
    pending_order_count: int


class BatchPageResponse(BaseModel):
    """A page of files, newest first."""

    items: list[BatchResponse]
    total: int
    page: int
    size: int


class CheckResponse(BaseModel):
    """One support checked against the line."""

    support_id: UUID
    kind: str
    calc_method: str
    program: str | None
    valid_from: date | None
    valid_to: date | None
    needs_review: bool
    outcome: Literal["cumple", "no_cumple", "no_evaluable"]
    detail: str
    effective_margin_pct: Decimal | None
    required_margin_pct: Decimal | None


class LineResponse(BaseModel):
    """A line of an order: what the file says and what the validator decided."""

    id: UUID
    row_number: int
    sku: str | None
    description: str | None
    supplier_code: str | None
    supplier_name: str | None
    unit_price: Decimal | None
    quantity: Decimal | None
    margin_pct: Decimal | None
    price_list: str | None
    warehouse: str | None
    problems: list[str]
    status: LineStatus
    status_label: str
    reason: Reason
    reason_label: str
    detail: str
    support_id: UUID | None
    effective_margin_pct: Decimal | None
    required_margin_pct: Decimal | None
    checks: list[CheckResponse]


class OrderResponse(BaseModel):
    """An order (document) with its lines; ready only when every line is."""

    document: str
    status: LineStatus
    status_label: str
    order_date: date | None
    operation_center: str | None
    status_text: str | None
    customer_nit: str | None
    customer_name: str | None
    salesperson: str | None
    channel: str | None
    line_count: int
    ready_line_count: int
    total_amount: Decimal | None
    lines: list[LineResponse]


class OrderPageResponse(BaseModel):
    """A page of orders and the order count per status (before the status filter)."""

    items: list[OrderResponse]
    total: int
    page: int
    size: int
    counts: dict[str, int]


class CatalogItem(BaseModel):
    """A value with its label."""

    value: str
    label: str
    status: str | None = None


class CatalogsResponse(BaseModel):
    """Statuses and reasons the screens show."""

    statuses: list[CatalogItem]
    reasons: list[CatalogItem]


class PermissionsResponse(BaseModel):
    """What the caller may do."""

    can_manage: bool


def _batch(batch: Batch) -> BatchResponse:
    return BatchResponse(
        id=batch.id,
        file_name=batch.file_name,
        uploaded_by=batch.uploaded_by,
        uploaded_at=batch.uploaded_at,
        validated_by=batch.validated_by,
        validated_at=batch.validated_at,
        line_count=batch.line_count,
        order_count=batch.order_count,
        ready_line_count=batch.ready_line_count,
        pending_line_count=batch.pending_line_count,
        ready_order_count=batch.ready_order_count,
        pending_order_count=batch.pending_order_count,
    )


def _check(item: AgreementCheck) -> CheckResponse:
    return CheckResponse(
        support_id=item.support_id,
        kind=item.kind,
        calc_method=item.calc_method,
        program=item.program,
        valid_from=item.valid_from,
        valid_to=item.valid_to,
        needs_review=item.needs_review,
        outcome=item.outcome,  # type: ignore[arg-type]
        detail=item.detail,
        effective_margin_pct=item.effective_margin_pct,
        required_margin_pct=item.required_margin_pct,
    )


def _line(item: StoredLine) -> LineResponse:
    line, result = item.line, item.result
    return LineResponse(
        id=item.id,
        row_number=line.row_number,
        sku=line.sku,
        description=line.description,
        supplier_code=line.supplier_code,
        supplier_name=line.supplier_name,
        unit_price=line.unit_price,
        quantity=line.quantity,
        margin_pct=line.margin_pct,
        price_list=line.price_list,
        warehouse=line.warehouse,
        problems=list(line.problems),
        status=result.status,  # type: ignore[arg-type]
        status_label=STATUS_LABELS[result.status],
        reason=result.reason,  # type: ignore[arg-type]
        reason_label=REASON_LABELS[result.reason],
        detail=result.detail,
        support_id=result.support_id,
        effective_margin_pct=result.effective_margin_pct,
        required_margin_pct=result.required_margin_pct,
        checks=[_check(check) for check in result.checks],
    )


def _order(order: OrderView) -> OrderResponse:
    first = order.first
    return OrderResponse(
        document=order.document,
        status=order.status,  # type: ignore[arg-type]
        status_label=STATUS_LABELS[order.status],
        order_date=first.order_date,
        operation_center=first.operation_center,
        status_text=first.status_text,
        customer_nit=first.customer_nit,
        customer_name=first.customer_name,
        salesperson=first.salesperson,
        channel=first.channel,
        line_count=len(order.lines),
        ready_line_count=order.ready_line_count,
        total_amount=order.total_amount,
        lines=[_line(item) for item in order.lines],
    )


@router.get("/permissions", response_model=PermissionsResponse, summary="What the caller may do")
@inject
async def permissions(
    principal: Principal = _principal,
    service: OrderValidationService = _service,
) -> PermissionsResponse:
    """Whether the caller may upload files and re-run validations."""

    return PermissionsResponse(can_manage=await service.can_manage(principal))


@router.get("/catalogs", response_model=CatalogsResponse, summary="Statuses and reasons")
@inject
async def catalogs(
    _: Principal = _principal,
    service: OrderValidationService = _service,
) -> CatalogsResponse:
    """Line and order statuses, and the reasons behind them, with their labels."""

    return CatalogsResponse.model_validate(service.catalogs())


@router.post(
    "",
    response_model=BatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a retained orders file",
    description=(
        "multipart/form-data with `file` (.xlsx, .xlsm or .csv, the SIESA retained orders "
        "report). Every line is checked against the supports master at once."
    ),
)
@inject
async def upload(
    file: UploadFile = File(...),  # noqa: B008
    principal: Principal = _principal,
    service: OrderValidationService = _service,
) -> BatchResponse:
    """Validate a file. Managers only."""

    # One byte over the limit is enough for the service to reject the file.
    content = await file.read(service.max_upload_bytes + 1)
    return _batch(await service.upload(principal, file.filename, content))


@router.get("", response_model=BatchPageResponse, summary="List uploaded files")
@inject
async def list_batches(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    _: Principal = _principal,
    service: OrderValidationService = _service,
) -> BatchPageResponse:
    """Uploaded files, newest first."""

    result = await service.list_batches(page, size)
    return BatchPageResponse(
        items=[_batch(item) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.get("/{batch_id}", response_model=BatchResponse, summary="Get an uploaded file")
@inject
async def get_batch(
    batch_id: UUID,
    _: Principal = _principal,
    service: OrderValidationService = _service,
) -> BatchResponse:
    """One file with its counts."""

    return _batch(await service.get_batch(batch_id))


@router.get("/{batch_id}/orders", response_model=OrderPageResponse, summary="Orders of a file")
@inject
async def list_orders(
    batch_id: UUID,
    status_filter: LineStatus | None = Query(default=None, alias="status"),  # noqa: B008
    reason: Reason | None = Query(default=None),  # noqa: B008
    q: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    _: Principal = _principal,
    service: OrderValidationService = _service,
) -> OrderPageResponse:
    """Orders of a file with every line, its decision and the supports checked."""

    result = await service.orders(
        batch_id, status=status_filter, reason=reason, q=q, page=page, size=size
    )
    return OrderPageResponse(
        items=[_order(item) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
        counts=result.counts,
    )


@router.post(
    "/{batch_id}/revalidate",
    response_model=BatchResponse,
    summary="Validate a file again",
    description="Checks every line again against the supports as they are now.",
)
@inject
async def revalidate(
    batch_id: UUID,
    principal: Principal = _principal,
    service: OrderValidationService = _service,
) -> BatchResponse:
    """Re-run the validation after supports changed. Managers only."""

    return _batch(await service.revalidate(principal, batch_id))


__all__ = ["router"]
