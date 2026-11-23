"""Endpoints of the retained orders validator."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, get_args
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.order_validation.application.service import (
    MAX_APPROVALS,
    MAX_NOTE_LENGTH,
    MAX_PAGE_SIZE,
    OrderValidationService,
)
from app.modules.order_validation.domain import rules
from app.modules.order_validation.domain.entities import (
    AgreementCheck,
    Batch,
    OrderView,
    StoredLine,
)
from app.modules.order_validation.domain.vocabulary import (
    ALERT_LABELS,
    REASON_LABELS,
    STATUS_LABELS,
)

router = APIRouter()
_READ_GATE = Depends(require_permission("validador-pedidos"))
_service = Depends(Provide[ApplicationContainer.v1_container.order_validation_service])
_principal = Depends(get_current_principal)
_TRY_THRESHOLD = Query(default=None, ge=0, le=20, description="Try a threshold without saving it")

LineStatus = Literal["listo_para_liberar", "pendiente_validacion"]
# An order also takes `liberado` once a person marks it as released.
OrderStatus = Literal["listo_para_liberar", "pendiente_validacion", "liberado"]
Reason = Literal[
    "cumple_con_apoyo",
    "cumple_margen_lista",
    "falta_impuesto_licor",
    "acuerdo_en_revision",
    "no_cumple_acuerdo",
    "acuerdo_no_evaluable",
    "acuerdo_vencido",
    "sin_acuerdo",
    "margen_100",
    "datos_incompletos",
]
ReleaseBasis = Literal["lineas", "margen", "tolerancia", "manual"]
AlertCode = Literal["margen_100", "bajo_objetivo", "falta_impuesto_licor"]
# The Salsamentarias margin was removed on 2026-11-23 (those customers use Mayoristas).
MarginBasis = Literal["lista", "mayoristas", "masivo_food", "market"]
_BASES = frozenset(get_args(MarginBasis))


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
    # Marked as released by a person; counted in neither ready nor pending.
    released_order_count: int
    margin_threshold_points: Decimal


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
    # The support brings $0 on this line: listed, never the reason the line is released.
    no_effect: bool = False


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
    margin_basis: MarginBasis | None
    liquor_tax_value: Decimal | None
    liquor_tax: bool
    liquor_tax_amount: Decimal | None
    order_margin_pct: Decimal | None = Field(
        description="The margin this line brings to the order margin: with its support when an "
        "active support with money decided it, else the file's"
    )
    points_below_target: Decimal | None = Field(
        description="Margin points (as a fraction) below its target, with its support; null when "
        "it is not below"
    )
    alerts: list[AlertCode]
    checks: list[CheckResponse]


class AlertResponse(BaseModel):
    """A warning on an order and the file rows behind it."""

    code: AlertCode
    label: str
    message: str
    row_numbers: list[int]


class ManualApprovalResponse(BaseModel):
    """Who released the order by hand, when and why."""

    approved_by: str
    approved_at: datetime
    note: str | None


class ApprovalRequest(BaseModel):
    """Pending orders of the file to release by hand, all or nothing."""

    documents: list[str] = Field(min_length=1, max_length=MAX_APPROVALS)
    note: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)


class ThresholdRequest(BaseModel):
    """The margin threshold of a file, in points."""

    margin_threshold_points: Decimal = Field(ge=0, le=20)


class RevalidateRequest(BaseModel):
    """Optional settings of a new validation."""

    margin_threshold_points: Decimal | None = Field(default=None, ge=0, le=20)


class OrderResponse(BaseModel):
    """An order with its lines: released when a person marked it, ready when every line is."""

    document: str
    manual_approval: ManualApprovalResponse | None
    status: OrderStatus
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
    margin_pct: Decimal | None
    required_margin_pct: Decimal | None
    margin_with_threshold_pct: Decimal | None
    release_basis: ReleaseBasis | None
    released_by_threshold: bool
    without_support_line_count: int
    has_liquor_tax: bool
    alerts: list[AlertResponse]
    lines: list[LineResponse]


class OperationCenterCount(BaseModel):
    """An operation center (C.O. factura) of the file and how many orders it has."""

    code: str
    order_count: int


class OrderPageResponse(BaseModel):
    """A page of orders and the order count per status (before the status filter)."""

    items: list[OrderResponse]
    total: int
    page: int
    size: int
    counts: dict[str, int]
    threshold_released_count: int
    # Every operation center of the whole file, whatever the filters, to offer them all.
    operation_centers: list[OperationCenterCount]


class CatalogItem(BaseModel):
    """A value with its label."""

    value: str
    label: str
    status: str | None = None


class CatalogsResponse(BaseModel):
    """Statuses, reasons and alerts the screens show."""

    statuses: list[CatalogItem]
    reasons: list[CatalogItem]
    alerts: list[CatalogItem]


class RuleResponse(BaseModel):
    """One rule of the validator, as people read it."""

    code: str
    title: str
    description: str
    effect: Literal["libera", "retiene", "alerta", "informa"]
    parameters: dict[str, str]


class RuleSectionResponse(BaseModel):
    """A group of rules."""

    code: str
    title: str
    rules: list[RuleResponse]


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
        released_order_count=batch.released_order_count,
        margin_threshold_points=(batch.margin_threshold * 100).quantize(Decimal("0.01")),
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
        no_effect=item.no_effect,
    )


def _line(item: StoredLine, order: OrderView) -> LineResponse:
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
        # Lines stored before 2026-11-17 may say «salsamentarias», a margin that no longer
        # exists: they show no basis.
        margin_basis=result.margin_basis if result.margin_basis in _BASES else None,  # type: ignore[arg-type]
        liquor_tax_value=line.liquor_tax_value,
        liquor_tax=result.liquor_tax,
        liquor_tax_amount=result.liquor_tax_amount,
        order_margin_pct=order.line_margins.get(item.id),
        points_below_target=rules.points_below_target(line, result),
        alerts=list(order.line_alerts.get(item.id, ())),  # type: ignore[arg-type]
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
        margin_pct=order.margin_pct,
        required_margin_pct=order.required_margin_pct,
        margin_with_threshold_pct=order.margin_with_threshold_pct,
        release_basis=order.release_basis,  # type: ignore[arg-type]
        released_by_threshold=order.released_by_threshold,
        without_support_line_count=order.without_support_line_count,
        has_liquor_tax=order.has_liquor_tax,
        alerts=[
            AlertResponse(
                code=alert.code,  # type: ignore[arg-type]
                label=ALERT_LABELS[alert.code],
                message=alert.message,
                row_numbers=list(alert.row_numbers),
            )
            for alert in order.alerts
        ],
        lines=[_line(item, order) for item in order.lines],
        manual_approval=(
            None
            if order.manual_approval is None
            else ManualApprovalResponse(
                approved_by=order.manual_approval.approved_by,
                approved_at=order.manual_approval.approved_at,
                note=order.manual_approval.note,
            )
        ),
    )


@router.get("/permissions", response_model=PermissionsResponse, summary="What the caller may do")
@inject
async def permissions(
    principal: Principal = _principal,
    service: OrderValidationService = _service,
) -> PermissionsResponse:
    """Whether the caller may upload files and re-run validations."""

    return PermissionsResponse(can_manage=await service.can_manage(principal))


@router.get(
    "/catalogs",
    dependencies=[_READ_GATE],
    response_model=CatalogsResponse,
    summary="Statuses and reasons",
)
@inject
async def catalogs(
    _: Principal = _principal,
    service: OrderValidationService = _service,
) -> CatalogsResponse:
    """Line and order statuses, and the reasons behind them, with their labels."""

    return CatalogsResponse.model_validate(service.catalogs())


@router.get(
    "/rules",
    dependencies=[_READ_GATE],
    response_model=list[RuleSectionResponse],
    summary="The rules the validator applies",
)
@inject
async def validation_rules(
    _: Principal = _principal,
    service: OrderValidationService = _service,
) -> list[RuleSectionResponse]:
    """Every rule, in the order it is applied and with the values in force, for the public
    «Reglas» page of the validator. Open to every user."""

    return [
        RuleSectionResponse(
            code=section.code,
            title=section.title,
            rules=[
                RuleResponse(
                    code=rule.code,
                    title=rule.title,
                    description=rule.description,
                    effect=rule.effect,  # type: ignore[arg-type]
                    parameters=dict(rule.parameters),
                )
                for rule in section.rules
            ],
        )
        for section in service.rule_catalog()
    ]


@router.post(
    "",
    dependencies=[_READ_GATE],
    response_model=BatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a retained orders file",
    description=(
        "multipart/form-data with `file` (.xlsx, .xlsm or .csv, the SIESA retained orders "
        "report). Every line is checked against the supports master at once. Optional form "
        "field ``margin_threshold_points`` (0.5 releases an order at 19.5 % against a 20 % target)."
    ),
)
@inject
async def upload(
    file: UploadFile = File(...),  # noqa: B008
    margin_threshold_points: Decimal | None = Form(default=None),  # noqa: B008
    principal: Principal = _principal,
    service: OrderValidationService = _service,
) -> BatchResponse:
    """Validate a file. Managers only."""

    # One byte over the limit is enough for the service to reject the file.
    content = await file.read(service.max_upload_bytes + 1)
    return _batch(await service.upload(principal, file.filename, content, margin_threshold_points))


@router.get(
    "", dependencies=[_READ_GATE], response_model=BatchPageResponse, summary="List uploaded files"
)
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


@router.get(
    "/{batch_id}",
    dependencies=[_READ_GATE],
    response_model=BatchResponse,
    summary="Get an uploaded file",
)
@inject
async def get_batch(
    batch_id: UUID,
    _: Principal = _principal,
    service: OrderValidationService = _service,
) -> BatchResponse:
    """One file with its counts."""

    return _batch(await service.get_batch(batch_id))


@router.get(
    "/{batch_id}/orders",
    dependencies=[_READ_GATE],
    response_model=OrderPageResponse,
    summary="Orders of a file",
)
@inject
async def list_orders(
    batch_id: UUID,
    status_filter: OrderStatus | None = Query(default=None, alias="status"),  # noqa: B008
    reason: Reason | None = Query(default=None),  # noqa: B008
    q: str | None = Query(default=None, max_length=100),
    operation_center: str | None = Query(default=None, max_length=20),
    margin_threshold_points: Decimal | None = _TRY_THRESHOLD,
    sort: Literal[
        "attention", "file", "document_asc", "document_desc", "margin_asc", "margin_desc"
    ] = Query(default="attention"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    _: Principal = _principal,
    service: OrderValidationService = _service,
) -> OrderPageResponse:
    """Orders of a file with every line, its decision and the supports checked.

    `attention` (default) lists first the orders, and inside each the lines, that have no
    support, then by margin from lowest to highest; `file` keeps the file order;
    `document_asc` / `document_desc` sort by the document number (the sales consecutive) in
    natural order, numbers as numbers, with documents without a number last;
    `margin_asc` / `margin_desc` by the order's margin, orders without one last, a tie by the
    consecutive and then the file order. With
    `margin_threshold_points` the statuses and counts come out as if the file had that
    threshold, without saving it; `PUT /{id}/margin-threshold` saves it. `operation_center`
    keeps the orders of one C.O. factura; `operation_centers` lists every one in the file.
    """

    result = await service.orders(
        batch_id,
        status=status_filter,
        reason=reason,
        q=q,
        operation_center=operation_center,
        margin_threshold_points=margin_threshold_points,
        sort=sort,
        page=page,
        size=size,
    )
    return OrderPageResponse(
        items=[_order(item) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
        counts=result.counts,
        threshold_released_count=result.threshold_released_count,
        operation_centers=[
            OperationCenterCount(code=code, order_count=count)
            for code, count in result.operation_centers
        ],
    )


@router.post(
    "/{batch_id}/revalidate",
    dependencies=[_READ_GATE],
    response_model=BatchResponse,
    summary="Validate a file again",
    description=(
        "Checks every line again against the supports and margins as they are now. The body "
        "is optional: ``margin_threshold_points`` sets the threshold of the file (the stored one "
        "is kept when omitted). Manual approvals are kept."
    ),
)
@inject
async def revalidate(
    batch_id: UUID,
    body: RevalidateRequest | None = None,
    principal: Principal = _principal,
    service: OrderValidationService = _service,
) -> BatchResponse:
    """Re-run the validation, optionally with a new margin threshold. Managers only."""

    points = body.margin_threshold_points if body else None
    return _batch(await service.revalidate(principal, batch_id, points))


@router.put(
    "/{batch_id}/margin-threshold",
    dependencies=[_READ_GATE],
    response_model=BatchResponse,
    summary="Save the margin threshold",
    description=(
        "Margin points an order may fall short by and still be released, added to the margin "
        "of each order. Lines are not re-checked, only which orders are released; manual "
        "approvals are kept."
    ),
)
@inject
async def set_margin_threshold(
    batch_id: UUID,
    body: ThresholdRequest,
    principal: Principal = _principal,
    service: OrderValidationService = _service,
) -> BatchResponse:
    """Save the threshold and recount the orders. Managers only."""

    return _batch(await service.set_threshold(principal, batch_id, body.margin_threshold_points))


@router.post(
    "/{batch_id}/approvals",
    dependencies=[_READ_GATE],
    response_model=BatchResponse,
    summary="Mark orders as released",
    description=(
        "Pending or ready orders become `liberado`. All or nothing: 404 if a document is not "
        "in the file, 409 if one is already released. The mark belongs to the order; its "
        "lines keep their result, and re-validating keeps it."
    ),
)
@inject
async def approve_orders(
    batch_id: UUID,
    body: ApprovalRequest,
    principal: Principal = _principal,
    service: OrderValidationService = _service,
) -> BatchResponse:
    """Mark orders as released. Managers only."""

    return _batch(await service.approve(principal, batch_id, body.documents, body.note))


@router.delete(
    "/{batch_id}/orders/{document}/approval",
    dependencies=[_READ_GATE],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Undo a release mark",
)
@inject
async def remove_approval(
    batch_id: UUID,
    document: str,
    principal: Principal = _principal,
    service: OrderValidationService = _service,
) -> Response:
    """The order goes back to what the validation says. Managers only."""

    await service.remove_approval(principal, batch_id, document)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{batch_id}",
    dependencies=[_READ_GATE],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an uploaded file",
)
@inject
async def delete_batch(
    batch_id: UUID,
    principal: Principal = _principal,
    service: OrderValidationService = _service,
) -> Response:
    """Delete the file with its lines, checks and approvals. Managers only."""

    await service.delete(principal, batch_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
