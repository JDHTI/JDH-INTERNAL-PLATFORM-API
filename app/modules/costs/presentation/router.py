"""Endpoints of Costos (Abastecimiento): the entries analysis and the novelties."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.core.errors import InvalidInputError
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.costs.application.access import CostAccess
from app.modules.costs.application.entries import MAX_PAGE_SIZE as ENTRIES_MAX_PAGE
from app.modules.costs.application.entries import EntryAnalysisService
from app.modules.costs.application.novelties import MAX_PAGE_SIZE as NOVELTIES_MAX_PAGE
from app.modules.costs.application.novelties import NoveltyService, UploadedFile
from app.modules.costs.domain.entries import (
    ENTRY_STATUS_LABELS,
    EntryFilters,
    EntryLine,
    EntryRow,
    FilterOptions,
)
from app.modules.costs.domain.modules import ENTRIES, MODULE_KEYS, NOVELTIES
from app.modules.costs.domain.novelties import (
    KIND_LABELS,
    NOVELTY_STATUS_LABELS,
    Novelty,
    NoveltyFilters,
    Reason,
)
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal

router = APIRouter()
_ENTRIES_GATE = Depends(require_permission(ENTRIES))
_NOVELTIES_GATE = Depends(require_permission(NOVELTIES))
_EITHER_GATE = Depends(require_permission(ENTRIES, NOVELTIES))
_access = Depends(Provide[ApplicationContainer.v1_container.cost_access])
_entries = Depends(Provide[ApplicationContainer.v1_container.cost_entry_service])
_novelties = Depends(Provide[ApplicationContainer.v1_container.cost_novelty_service])
_principal = Depends(get_current_principal)
_SINCE = Query(default=None, description="First day (YYYY-MM-DD)")
_UNTIL = Query(default=None, description="Last day (YYYY-MM-DD)")
_STATUS = Query(default=None, alias="status")
_OPTIONAL = Query(default=None)

EntryStatus = Literal["ok", "novedad", "sin_cotizacion"]
EntrySort = Literal["date", "difference", "difference_pct", "amount", "sku", "supplier"]
NoveltyStatus = Literal["pendiente", "espera_nota_credito", "resuelta", "anulada"]
NoveltyKind = Literal["diferencia", "sin_cotizacion", "sin_novedad"]
NoveltySort = Literal["date", "difference", "difference_pct", "sku", "supplier"]
Order = Literal["asc", "desc"]


# --- Schemas ---


class ModuleAccessResponse(BaseModel):
    can_read: bool
    can_manage: bool


class PermissionsResponse(BaseModel):
    """What the caller may do in each Costos view, by permission key."""

    modules: dict[str, ModuleAccessResponse]
    # Whether the caller may give reasons (manages the entries or the novelties).
    can_set_reasons: bool


class ReasonResponse(BaseModel):
    id: UUID
    code: str
    name: str
    requires_credit_note: bool
    is_active: bool
    sort_order: int


class ReasonPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    requires_credit_note: bool | None = None
    is_active: bool | None = None


class ReasonChoice(BaseModel):
    """The one reason of a line or novelty; ``null`` takes it away."""

    reason_id: UUID | None


class LineResponse(BaseModel):
    """A purchase entry line against the quotation in force on its day."""

    line_key: str
    entry_date: date
    co: str | None
    regional_code: str | None
    regional_name: str | None
    warehouse: str | None
    document: str | None
    purchase_order: str | None
    invoice_ref: str | None
    sku: str
    description: str | None
    supplier_code: str | None
    supplier_name: str | None
    supplier_nit: str | None
    quantity: Decimal | None
    unit: str | None
    # Purchase prices: this entry's, the previous one and the one before it.
    unit_price: Decimal
    previous_price: Decimal | None
    antepenultimate_price: Decimal | None
    # What the entry should have cost: the quotation in force less its Tasa 1.
    quotation: Decimal | None
    quotation_since: date | None
    # Tasa 1 as a fraction (0.2 is 20 %) and the quotation's unit price before it.
    discount_pct: Decimal | None
    quotation_list_price: Decimal | None
    difference: Decimal | None
    difference_pct: Decimal | None
    amount: Decimal | None
    status: EntryStatus
    status_label: str


class NoveltyBriefResponse(BaseModel):
    id: UUID
    status: NoveltyStatus
    status_label: str
    reason_id: UUID | None
    reason_name: str | None
    credit_note_number: str | None
    credit_note_amount: Decimal | None


class EntryResponse(LineResponse):
    # What the platform keeps of the line: ``null`` when nobody gave it a reason and the job
    # has not copied it.
    novelty: NoveltyBriefResponse | None


class EntryPageResponse(BaseModel):
    items: list[EntryResponse]
    total: int
    page: int
    size: int
    since: date
    until: date
    counts: dict[str, int]
    fetched_at: datetime | None
    previous_from_warehouse: bool
    threshold: Decimal


class CenterOptionResponse(BaseModel):
    code: str
    # The center's SIESA name («CENTRO»), or its regional's when the warehouse has none.
    name: str | None


class WarehouseOptionResponse(BaseModel):
    code: str
    co: str | None


class FilterOptionsResponse(BaseModel):
    """The operation centers and warehouses of a period, by code (the dropdowns)."""

    cos: list[CenterOptionResponse]
    warehouses: list[WarehouseOptionResponse]


def _options(options: FilterOptions) -> FilterOptionsResponse:
    return FilterOptionsResponse(
        cos=[CenterOptionResponse(code=item.code, name=item.name) for item in options.cos],
        warehouses=[
            WarehouseOptionResponse(code=item.code, co=item.co) for item in options.warehouses
        ],
    )


class CreditNoteResponse(BaseModel):
    id: UUID
    supplier_nit: str
    number: str
    amount: Decimal
    original_filename: str
    content_type: str
    size_bytes: int
    registered_by: str
    registered_at: datetime


class NoveltyEventResponse(BaseModel):
    id: UUID
    event_type: str
    actor: str | None
    data: dict[str, Any]
    created_at: datetime


class NoveltyResponse(BaseModel):
    id: UUID
    line: LineResponse
    kind: NoveltyKind
    kind_label: str
    status: NoveltyStatus
    status_label: str
    voided: bool
    reason: ReasonResponse | None
    reason_by: str | None
    reason_at: datetime | None
    credit_note: CreditNoteResponse | None
    detected_at: datetime
    updated_at: datetime


class NoveltyDetailResponse(NoveltyResponse):
    events: list[NoveltyEventResponse]


class NoveltyPageResponse(BaseModel):
    items: list[NoveltyResponse]
    total: int
    page: int
    size: int
    counts: dict[str, int]


# --- Mapping ---


def _reason(reason: Reason) -> ReasonResponse:
    return ReasonResponse(
        id=reason.id,
        code=reason.code,
        name=reason.name,
        requires_credit_note=reason.requires_credit_note,
        is_active=reason.is_active,
        sort_order=reason.sort_order,
    )


def _line_fields(line: EntryLine) -> dict[str, Any]:
    return {
        "line_key": line.line_key,
        "entry_date": line.entry_date,
        "co": line.co,
        "regional_code": line.regional_code,
        "regional_name": line.regional_name,
        "warehouse": line.warehouse,
        "document": line.document,
        "purchase_order": line.purchase_order,
        "invoice_ref": line.invoice_ref,
        "sku": line.sku,
        "description": line.description,
        "supplier_code": line.supplier_code,
        "supplier_name": line.supplier_name,
        "supplier_nit": line.supplier_nit,
        "quantity": line.quantity,
        "unit": line.unit,
        "unit_price": line.unit_price,
        "previous_price": line.previous_price,
        "antepenultimate_price": line.antepenultimate_price,
        "quotation": line.quotation,
        "quotation_since": line.quotation_since,
        "discount_pct": line.discount_pct,
        "quotation_list_price": line.quotation_list_price,
        "difference": line.difference,
        "difference_pct": None if line.difference_pct is None else round(line.difference_pct, 6),
        "amount": line.amount,
        "status": line.status,
        "status_label": ENTRY_STATUS_LABELS[line.status],
    }


def _entry(row: EntryRow) -> EntryResponse:
    brief = row.novelty
    return EntryResponse(
        **_line_fields(row.line),
        novelty=(
            None
            if brief is None
            else NoveltyBriefResponse(
                id=brief.id,
                status=brief.status,
                status_label=NOVELTY_STATUS_LABELS[brief.status],
                reason_id=brief.reason_id,
                reason_name=brief.reason_name,
                credit_note_number=brief.credit_note_number,
                credit_note_amount=brief.credit_note_amount,
            )
        ),
    )


def _novelty_fields(novelty: Novelty) -> dict[str, Any]:
    note = novelty.credit_note
    return {
        "id": novelty.id,
        "line": LineResponse(**_line_fields(novelty.line)),
        "kind": novelty.kind,
        "kind_label": KIND_LABELS[novelty.kind],
        "status": novelty.status,
        "status_label": NOVELTY_STATUS_LABELS[novelty.status],
        "voided": novelty.voided,
        "reason": _reason(novelty.reason) if novelty.reason else None,
        "reason_by": novelty.reason_by,
        "reason_at": novelty.reason_at,
        "credit_note": (
            None
            if note is None
            else CreditNoteResponse(
                id=note.id,
                supplier_nit=note.supplier_nit,
                number=note.number,
                amount=note.amount,
                original_filename=note.original_filename,
                content_type=note.content_type,
                size_bytes=note.size_bytes,
                registered_by=note.registered_by,
                registered_at=note.registered_at,
            )
        ),
        "detected_at": novelty.detected_at,
        "updated_at": novelty.updated_at,
    }


def _detail(novelty: Novelty) -> NoveltyDetailResponse:
    return NoveltyDetailResponse(
        **_novelty_fields(novelty),
        events=[
            NoveltyEventResponse(
                id=event.id,
                event_type=event.event_type,
                actor=event.actor,
                data=event.data,
                created_at=event.created_at,
            )
            for event in novelty.events
        ],
    )


# --- Permissions and reasons ---


@router.get("/permissions", response_model=PermissionsResponse, summary="What the caller may do")
@inject
async def permissions(
    principal: Principal = _principal,
    access: CostAccess = _access,
) -> PermissionsResponse:
    """``read`` and ``manage`` of each Costos view; open, so a person without them gets
    ``false`` instead of a 403."""

    modules = await access.permissions(principal)
    return PermissionsResponse(
        modules={
            key: ModuleAccessResponse(
                can_read=modules[key].can_read, can_manage=modules[key].can_manage
            )
            for key in MODULE_KEYS
        },
        can_set_reasons=modules[ENTRIES].can_manage or modules[NOVELTIES].can_manage,
    )


@router.get(
    "/reasons",
    dependencies=[_EITHER_GATE],
    response_model=list[ReasonResponse],
    summary="Reasons of the entries",
)
@inject
async def reasons(
    include_inactive: bool = Query(default=False, description="Also the switched-off reasons"),
    service: NoveltyService = _novelties,
) -> list[ReasonResponse]:
    """The reasons a line or novelty can take, in their order (the filterable dropdown)."""

    return [_reason(reason) for reason in await service.reasons(include_inactive)]


@router.patch(
    "/reasons/{reason_id}",
    dependencies=[_NOVELTIES_GATE],
    response_model=ReasonResponse,
    summary="Change a reason",
)
@inject
async def update_reason(
    reason_id: UUID,
    body: ReasonPatch,
    principal: Principal = _principal,
    service: NoveltyService = _novelties,
) -> ReasonResponse:
    """Rename a reason, say whether it needs a credit note, or switch it off
    (``novedades-compras: manage``)."""

    reason = await service.update_reason(
        principal,
        reason_id,
        name=body.name,
        requires_credit_note=body.requires_credit_note,
        is_active=body.is_active,
    )
    return _reason(reason)


# --- Entries analysis ---


@router.get(
    "/entries",
    dependencies=[_ENTRIES_GATE],
    response_model=EntryPageResponse,
    summary="Purchase entries against the quotation",
)
@inject
async def entries(
    since: date | None = _SINCE,
    until: date | None = _UNTIL,
    q: str | None = Query(
        default=None, max_length=120, description="Reference, description, document"
    ),
    status_filter: EntryStatus | None = _STATUS,
    novelty: bool = Query(default=False, description="Only lines with a novelty"),
    supplier: str | None = Query(
        default=None, max_length=20, description="Commercial supplier code"
    ),
    co: str | None = Query(default=None, max_length=10),
    warehouse: str | None = Query(default=None, max_length=20),
    reason_id: UUID | None = _OPTIONAL,
    sort: EntrySort = "date",
    order: Order = "desc",
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=ENTRIES_MAX_PAGE),
    service: EntryAnalysisService = _entries,
) -> EntryPageResponse:
    """The purchase entries of a period, each against the quotation in force on its day, with
    the counts by status of the search and what the platform keeps of each line."""

    result = await service.page(
        since=since,
        until=until,
        filters=EntryFilters(
            q=q,
            status=status_filter,
            novelty_only=novelty,
            supplier=supplier,
            co=co,
            warehouse=warehouse,
            reason_id=reason_id,
        ),
        sort=sort,
        descending=order == "desc",
        page=page,
        size=size,
    )
    return EntryPageResponse(
        items=[_entry(row) for row in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
        since=result.since,
        until=result.until,
        counts=result.counts,
        fetched_at=result.fetched_at,
        previous_from_warehouse=result.previous_from_warehouse,
        threshold=service.threshold,
    )


@router.get(
    "/entries/options",
    dependencies=[_ENTRIES_GATE],
    response_model=FilterOptionsResponse,
    summary="Operation centers and warehouses of the entries",
)
@inject
async def entry_options(
    since: date | None = _SINCE,
    until: date | None = _UNTIL,
    service: EntryAnalysisService = _entries,
) -> FilterOptionsResponse:
    """The operation centers and warehouses with entries in the period (the last 30 days by
    default), by code, for the analysis' dropdowns."""

    return _options(await service.options(since, until))


@router.put(
    "/entries/{line_key}/reason",
    dependencies=[_EITHER_GATE],
    response_model=NoveltyDetailResponse,
    summary="Give an entry line its reason",
)
@inject
async def set_entry_reason(
    line_key: str,
    body: ReasonChoice,
    principal: Principal = _principal,
    service: NoveltyService = _novelties,
) -> NoveltyDetailResponse:
    """The one reason of a line; the line becomes a novelty if it was not one yet
    (``analisis-entradas`` or ``novedades-compras``: manage)."""

    if not line_key or len(line_key) > 64:
        raise InvalidInputError("Línea inválida")
    return _detail(await service.set_reason_for_line(principal, line_key, body.reason_id))


# --- Novelties ---


@router.get(
    "/novelties",
    dependencies=[_NOVELTIES_GATE],
    response_model=NoveltyPageResponse,
    summary="Novelties of the entries",
)
@inject
async def novelties(
    status_filter: NoveltyStatus | None = _STATUS,
    since: date | None = _SINCE,
    until: date | None = _UNTIL,
    supplier: str | None = Query(default=None, max_length=20),
    co: str | None = Query(default=None, max_length=10),
    warehouse: str | None = Query(default=None, max_length=20),
    reason_id: UUID | None = _OPTIONAL,
    kind: NoveltyKind | None = _OPTIONAL,
    q: str | None = Query(default=None, max_length=120),
    sort: NoveltySort = "date",
    order: Order = "desc",
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=NOVELTIES_MAX_PAGE),
    service: NoveltyService = _novelties,
) -> NoveltyPageResponse:
    """The novelties with their reason and credit note, and the counts by status."""

    result = await service.page(
        NoveltyFilters(
            status=status_filter,
            since=since,
            until=until,
            supplier=supplier,
            co=co,
            warehouse=warehouse,
            reason_id=reason_id,
            kind=kind,
            q=q,
        ),
        sort,
        order == "desc",
        page,
        size,
    )
    return NoveltyPageResponse(
        items=[NoveltyResponse(**_novelty_fields(item)) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
        counts=result.counts,
    )


# Before ``/novelties/{novelty_id}``: «options» is not an identifier.
@router.get(
    "/novelties/options",
    dependencies=[_NOVELTIES_GATE],
    response_model=FilterOptionsResponse,
    summary="Operation centers and warehouses of the novelties",
)
@inject
async def novelty_options(
    since: date | None = _SINCE,
    until: date | None = _UNTIL,
    service: NoveltyService = _novelties,
) -> FilterOptionsResponse:
    """The operation centers and warehouses of the novelties of the period (every novelty
    without dates), by code, for the tray's dropdowns."""

    return _options(await service.options(since, until))


@router.get(
    "/novelties/{novelty_id}",
    dependencies=[_EITHER_GATE],
    response_model=NoveltyDetailResponse,
    summary="A novelty and its history",
)
@inject
async def novelty(novelty_id: UUID, service: NoveltyService = _novelties) -> NoveltyDetailResponse:
    """One novelty with its reason, credit note and history."""

    return _detail(await service.get(novelty_id))


@router.put(
    "/novelties/{novelty_id}/reason",
    dependencies=[_EITHER_GATE],
    response_model=NoveltyDetailResponse,
    summary="Give a novelty its reason",
)
@inject
async def set_novelty_reason(
    novelty_id: UUID,
    body: ReasonChoice,
    principal: Principal = _principal,
    service: NoveltyService = _novelties,
) -> NoveltyDetailResponse:
    """The one reason of a novelty, or none (``analisis-entradas`` or ``novedades-compras``:
    manage)."""

    return _detail(await service.set_reason(principal, novelty_id, body.reason_id))


@router.post(
    "/novelties/{novelty_id}/credit-note",
    dependencies=[_NOVELTIES_GATE],
    response_model=NoveltyDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register the supplier's credit note",
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["number", "amount"],
                        "properties": {
                            "number": {"type": "string", "maxLength": 60},
                            "amount": {"type": "string", "description": "Pesos, e.g. 12000.50"},
                            "file": {"type": "string", "format": "binary"},
                        },
                    }
                }
            }
        }
    },
)
@inject
async def attach_credit_note(
    novelty_id: UUID,
    request: Request,
    principal: Principal = _principal,
    service: NoveltyService = _novelties,
) -> NoveltyDetailResponse:
    """Number, amount and PDF or picture of the supplier's credit note
    (``novedades-compras: manage``). A number the supplier already has reuses its file."""

    form = await request.form(max_files=1, max_fields=5)
    upload = form.get("file")
    file = None
    if upload is not None and hasattr(upload, "read"):
        limit = service.guard.max_attachment_bytes
        file = UploadedFile(upload.filename or "nota-credito", await upload.read(limit + 1))
    novelty = await service.attach_credit_note(
        principal,
        novelty_id,
        number=str(form.get("number") or ""),
        amount=str(form.get("amount") or ""),
        upload=file,
    )
    return _detail(novelty)


@router.delete(
    "/novelties/{novelty_id}/credit-note",
    dependencies=[_NOVELTIES_GATE],
    response_model=NoveltyDetailResponse,
    summary="Take the credit note off",
)
@inject
async def detach_credit_note(
    novelty_id: UUID,
    principal: Principal = _principal,
    service: NoveltyService = _novelties,
) -> NoveltyDetailResponse:
    """Remove the credit note of a novelty (``novedades-compras: manage``)."""

    return _detail(await service.detach_credit_note(principal, novelty_id))


@router.get(
    "/novelties/{novelty_id}/credit-note/file",
    dependencies=[_EITHER_GATE],
    summary="Download the credit note file",
)
@inject
async def credit_note_file(
    novelty_id: UUID,
    inline: bool = Query(default=False, description="Show it in the browser"),
    service: NoveltyService = _novelties,
) -> Response:
    """The PDF or picture of a novelty's credit note."""

    note, content = await service.credit_note_file(novelty_id)
    name = note.original_filename
    disposition = "inline" if inline else "attachment"
    return Response(
        content=content,
        media_type=note.content_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(name)}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = ["router"]
