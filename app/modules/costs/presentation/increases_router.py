"""Endpoints of the supplier increase requests (Costos → Alzas de proveedor)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.costs.application.increases import (
    MAX_NOTICE_REQUESTS,
    MAX_PAGE_SIZE,
    IncreaseService,
    LineChanges,
)
from app.modules.costs.application.novelties import UploadedFile
from app.modules.costs.domain.increases import (
    CHANGE_LABELS,
    MAX_LINES,
    REQUEST_STATUS_LABELS,
    IncreaseRequest,
    LineInput,
    RequestLine,
    SupplierItem,
    UploadReport,
)
from app.modules.costs.domain.modules import INCREASES
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal

router = APIRouter(dependencies=[Depends(require_permission(INCREASES))])
_service = Depends(Provide[ApplicationContainer.v1_container.cost_increase_service])
_principal = Depends(get_current_principal)
_FILE = File(...)
_NOTE = Form(default=None, max_length=500)
_SUPPORT = File(default=None)
_STATUS = Query(default=None, alias="status")
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

RequestStatus = Literal["en_revision", "aprobada", "rechazada"]
Regional = Literal[
    "NACIONAL",
    "ANTIOQUIA",
    "BOGOTA",
    "PEREIRA",
    "BUCARAMANGA",
    "CARTAGENA",
    "BARRANQUILLA",
    "RESTO DE REGIONALES",
]


class SupplierItemResponse(BaseModel):
    """An active product of the supplier, as the form offers it."""

    sku: str
    supplier_sku: str | None
    ean: str | None
    nit: str | None
    description: str | None
    pack_factor: Decimal | None
    category: str | None
    tax_group: str | None
    icui_pct: Decimal | None
    ibua: Decimal | None
    old_cost: Decimal | None
    old_cost_with_tax: Decimal | None


class LineResponse(BaseModel):
    id: UUID
    row_number: int | None
    sku: str
    supplier_sku: str | None
    ean: str | None
    description: str | None
    pack_factor: Decimal | None
    category: str | None
    tax_group: str | None
    icui_pct: Decimal | None
    ibua: Decimal | None
    old_cost: Decimal | None
    old_cost_with_tax: Decimal | None
    new_cost: Decimal
    new_cost_with_tax: Decimal
    variation_amount: Decimal | None
    variation_pct: Decimal | None
    change: Literal["subio", "bajo", "estable"] | None
    change_label: str | None
    increased_on: date | None
    regular_discount_pct: Decimal | None
    # NACIONAL, a regional or RESTO DE REGIONALES, and the regionals it holds for.
    regional: Regional
    regionals: list[str]
    # The public sale price without VAT the supplier suggests (PVP).
    public_price: Decimal | None
    observation: str | None
    accepted: bool
    ledger_id: UUID | None


class SupportResponse(BaseModel):
    """The supplier's support of the increase; download it from ``…/support``."""

    file_name: str
    content_type: str
    size_bytes: int
    uploaded_by: str
    uploaded_at: datetime


class RequestResponse(BaseModel):
    id: UUID
    supplier_code: str
    supplier_name: str | None
    source: Literal["plantilla", "manual"]
    status: RequestStatus
    status_label: str
    file_name: str | None
    has_file: bool
    # null without one.
    support: SupportResponse | None
    note: str | None
    created_by: str
    created_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    line_count: int
    accepted_count: int
    # Approved lines SIESA already has (a confirmed load): what the notice takes.
    loaded_count: int


class NoticeBody(BaseModel):
    """These requests, or every approved one (``all``), of ``supplier`` when given."""

    request_ids: list[UUID] = Field(default_factory=list, max_length=MAX_NOTICE_REQUESTS)
    all: bool = False
    supplier: str | None = Field(default=None, max_length=20)


class RequestDetailResponse(RequestResponse):
    lines: list[LineResponse]


class RequestPageResponse(BaseModel):
    items: list[RequestResponse]
    total: int
    page: int
    size: int
    counts: dict[str, int]


class IssueResponse(BaseModel):
    row_number: int
    sku: str | None
    problem: str


class UploadResponse(BaseModel):
    """What the workbook would create (``applied`` false) or created."""

    applied: bool
    blocked: bool
    supplier_code: str
    rows_read: int
    lines: list[LineResponse]
    issues: list[IssueResponse]
    request_id: UUID | None


class LineBody(BaseModel):
    """One reference of a manual request. Percentages are fractions (0.05 is 5 %)."""

    sku: str = Field(min_length=1, max_length=40)
    new_cost: Decimal = Field(gt=0)
    increased_on: date
    supplier_sku: str | None = Field(default=None, max_length=60)
    ean: str | None = Field(default=None, max_length=40)
    icui_pct: Decimal | None = Field(default=None, ge=0, le=1)
    ibua: Decimal | None = Field(default=None, ge=0)
    regular_discount_pct: Decimal | None = Field(default=None, ge=0, le=1)
    regional: Regional = "NACIONAL"
    public_price: Decimal | None = Field(default=None, gt=0)
    observation: str | None = Field(default=None, max_length=500)


class RequestBody(BaseModel):
    supplier_code: str = Field(min_length=1, max_length=20)
    note: str | None = Field(default=None, max_length=500)
    lines: list[LineBody] = Field(min_length=1, max_length=MAX_LINES)


class LinePatch(BaseModel):
    accepted: bool | None = None
    new_cost: Decimal | None = Field(default=None, gt=0)
    increased_on: date | None = None
    regular_discount_pct: Decimal | None = Field(default=None, ge=0, le=1)
    public_price: Decimal | None = Field(default=None, gt=0)
    observation: str | None = Field(default=None, max_length=500)


class DecisionBody(BaseModel):
    note: str | None = Field(default=None, max_length=500)


def _item(item: SupplierItem) -> SupplierItemResponse:
    return SupplierItemResponse(
        sku=item.sku,
        supplier_sku=item.supplier_sku,
        ean=item.ean,
        nit=item.nit,
        description=item.description,
        pack_factor=item.pack_factor,
        category=item.category,
        tax_group=item.tax_group,
        icui_pct=item.icui_pct,
        ibua=item.ibua,
        old_cost=item.old_cost,
        old_cost_with_tax=item.old_cost_with_tax,
    )


def _line(line: RequestLine) -> LineResponse:
    return LineResponse(
        id=line.id,
        row_number=line.row_number,
        sku=line.sku,
        supplier_sku=line.supplier_sku,
        ean=line.ean,
        description=line.description,
        pack_factor=line.pack_factor,
        category=line.category,
        tax_group=line.tax_group,
        icui_pct=line.icui_pct,
        ibua=line.ibua,
        old_cost=line.old_cost,
        old_cost_with_tax=line.old_cost_with_tax,
        new_cost=line.new_cost,
        new_cost_with_tax=line.new_cost_with_tax,
        variation_amount=line.variation_amount,
        variation_pct=line.variation_pct,
        change=line.change,
        change_label=CHANGE_LABELS.get(line.change or ""),
        increased_on=line.increased_on,
        regular_discount_pct=line.regular_discount_pct,
        regional=line.regional,
        regionals=list(line.regionals),
        public_price=line.public_price,
        observation=line.observation,
        accepted=line.accepted,
        ledger_id=line.ledger_id,
    )


def _summary(request: IncreaseRequest) -> dict:
    return {
        "id": request.id,
        "supplier_code": request.supplier_code,
        "supplier_name": request.supplier_name,
        "source": request.source,
        "status": request.status,
        "status_label": REQUEST_STATUS_LABELS[request.status],
        "file_name": request.file_name,
        "has_file": request.storage_key is not None,
        "support": None
        if request.support is None
        else SupportResponse(
            file_name=request.support.file_name,
            content_type=request.support.content_type,
            size_bytes=request.support.size_bytes,
            uploaded_by=request.support.uploaded_by,
            uploaded_at=request.support.uploaded_at,
        ),
        "note": request.note,
        "created_by": request.created_by,
        "created_at": request.created_at,
        "decided_by": request.decided_by,
        "decided_at": request.decided_at,
        "decision_note": request.decision_note,
        "line_count": request.line_count,
        "accepted_count": request.accepted_count,
        "loaded_count": request.loaded_count,
    }


def _detail(request: IncreaseRequest) -> RequestDetailResponse:
    return RequestDetailResponse(**_summary(request), lines=[_line(line) for line in request.lines])


def _report(report: UploadReport) -> UploadResponse:
    return UploadResponse(
        applied=report.applied,
        blocked=report.blocked,
        supplier_code=report.supplier_code,
        rows_read=report.rows_read,
        lines=[_line(line) for line in report.lines],
        issues=[
            IssueResponse(row_number=i.row_number, sku=i.sku, problem=i.problem)
            for i in report.issues
        ],
        request_id=report.request_id,
    )


async def _uploaded(file: UploadFile | None, service: IncreaseService) -> UploadedFile | None:
    """The file's name and bytes, read up to one byte past the largest support (the guard then
    turns it away); ``None`` without a file."""

    if file is None or not file.filename:
        return None
    guard = service.support_guard
    content = await file.read((guard.max_attachment_bytes if guard else 0) + 1)
    return UploadedFile(file.filename, content)


def _download(name: str, content: bytes) -> Response:
    return Response(
        content=content,
        media_type=_XLSX,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "Cache-Control": "no-store",
        },
    )


@router.get(
    "/increase-requests/items",
    response_model=list[SupplierItemResponse],
    summary="Active products of a supplier",
)
@inject
async def supplier_items(
    supplier_code: str = Query(min_length=1, max_length=20),
    q: str | None = Query(default=None, max_length=120),
    service: IncreaseService = _service,
) -> list[SupplierItemResponse]:
    """The supplier's active products with their old cost, for the manual form."""

    return [_item(item) for item in await service.items(supplier_code, q)]


@router.get("/increase-requests/template", summary="The supplier's MAESTRA workbook")
@inject
async def template(
    supplier_code: str = Query(min_length=1, max_length=20),
    service: IncreaseService = _service,
) -> Response:
    """``MAESTRA <código> - <proveedor> <dd-mm-aaaa>.xlsx`` with every active product, its old
    cost and the formulas of the variation."""

    name, content = await service.template(supplier_code)
    return _download(name, content)


@router.post(
    "/increase-requests/upload",
    response_model=UploadResponse,
    summary="Load a returned MAESTRA",
)
@inject
async def upload(
    supplier_code: str = Query(min_length=1, max_length=20),
    apply: bool = Query(default=False, description="Store it; a simulation otherwise"),
    file: UploadFile = _FILE,
    note: str | None = _NOTE,
    support: UploadFile | None = _SUPPORT,
    principal: Principal = _principal,
    service: IncreaseService = _service,
) -> UploadResponse:
    """The rows with a new cost become a request in review (``alzas-proveedor: manage``).
    Any problem blocks it; the report lists each one by sheet row.

    ``support`` (optional) is the supplier's support of the increase: PDF, picture, Excel or
    Word, up to 10 MB. It is kept under the area in the bucket with the request."""

    content = await file.read(service.max_upload_bytes + 1)
    report = await service.upload(
        principal,
        supplier_code,
        file.filename,
        content,
        apply=apply,
        note=note,
        support=await _uploaded(support, service),
    )
    return _report(report)


@router.post(
    "/increase-requests",
    response_model=RequestDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Type a request by hand",
)
@inject
async def create(
    body: RequestBody,
    principal: Principal = _principal,
    service: IncreaseService = _service,
) -> RequestDetailResponse:
    """A few references of one supplier, in review (``alzas-proveedor: manage``)."""

    lines = [
        LineInput(
            sku=line.sku.strip(),
            new_cost=line.new_cost,
            increased_on=line.increased_on,
            supplier_sku=line.supplier_sku,
            ean=line.ean,
            icui_pct=line.icui_pct,
            ibua=line.ibua,
            regular_discount_pct=line.regular_discount_pct,
            regional=line.regional,
            public_price=line.public_price,
            observation=(line.observation or "").strip().upper() or None,
        )
        for line in body.lines
    ]
    return _detail(await service.create(principal, body.supplier_code, lines, body.note))


@router.post("/increase-requests/notice", summary="The increase notice of approved requests")
@inject
async def notice(body: NoticeBody, service: IncreaseService = _service) -> Response:
    """«Comunicado Alza de precios proveedor»: one row per approved line SIESA already has
    (its load confirmed in the history), to email. ``request_ids`` picks the requests one by
    one; ``all`` takes every approved one (of ``supplier``). Lines not yet in SIESA are left
    out and counted in ``X-Notice-Skipped``; 422 when none is in SIESA."""

    name, content, rows, skipped = await service.notice(
        body.request_ids, all_approved=body.all, supplier=body.supplier
    )
    response = _download(name, content)
    response.headers["X-Notice-Rows"] = str(rows)
    response.headers["X-Notice-Skipped"] = str(skipped)
    return response


@router.get("/increase-requests", response_model=RequestPageResponse, summary="Increase requests")
@inject
async def requests(
    status_filter: RequestStatus | None = _STATUS,
    supplier: str | None = Query(default=None, max_length=20),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    service: IncreaseService = _service,
) -> RequestPageResponse:
    """The requests, the latest first, with the counts by status."""

    result = await service.page(status_filter, supplier, page, size)
    return RequestPageResponse(
        items=[RequestResponse(**_summary(item)) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
        counts=result.counts,
    )


@router.get(
    "/increase-requests/{request_id}",
    response_model=RequestDetailResponse,
    summary="A request and its lines",
)
@inject
async def request(request_id: UUID, service: IncreaseService = _service) -> RequestDetailResponse:
    """One request with its lines and the regionals each one covers."""

    return _detail(await service.get(request_id))


@router.get("/increase-requests/{request_id}/file", summary="The returned workbook")
@inject
async def request_file(request_id: UUID, service: IncreaseService = _service) -> Response:
    """The «MAESTRA» the supplier returned, as it was loaded."""

    name, content = await service.file(request_id)
    return _download(name, content)


@router.put(
    "/increase-requests/{request_id}/support",
    response_model=RequestDetailResponse,
    summary="Attach the supplier's support",
)
@inject
async def attach_support(
    request_id: UUID,
    file: UploadFile = _FILE,
    principal: Principal = _principal,
    service: IncreaseService = _service,
) -> RequestDetailResponse:
    """Attach or replace the support of a request in review (``alzas-proveedor: manage``):
    PDF, picture, Excel or Word, up to 10 MB. A request typed by hand gets it this way."""

    upload = await _uploaded(file, service)
    return _detail(
        await service.attach_support(principal, request_id, upload or UploadedFile("", b""))
    )


@router.delete(
    "/increase-requests/{request_id}/support",
    response_model=RequestDetailResponse,
    summary="Remove the supplier's support",
)
@inject
async def remove_support(
    request_id: UUID,
    principal: Principal = _principal,
    service: IncreaseService = _service,
) -> RequestDetailResponse:
    """Only while the request is in review (``alzas-proveedor: manage``)."""

    return _detail(await service.remove_support(principal, request_id))


@router.get("/increase-requests/{request_id}/support", summary="Download the supplier's support")
@inject
async def download_support(
    request_id: UUID,
    inline: bool = Query(default=False, description="Open it in the browser"),
    service: IncreaseService = _service,
) -> Response:
    """The support as it was kept (whoever reads the increases)."""

    support, content = await service.support(request_id)
    disposition = "inline" if inline else "attachment"
    return Response(
        content=content,
        media_type=support.content_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(support.file_name)}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.patch(
    "/increase-requests/{request_id}/lines/{line_id}",
    response_model=RequestDetailResponse,
    summary="Correct a line in review",
)
@inject
async def update_line(
    request_id: UUID,
    line_id: UUID,
    body: LinePatch,
    principal: Principal = _principal,
    service: IncreaseService = _service,
) -> RequestDetailResponse:
    """Correct the new cost, date, discounts or observation of a line, or leave it out
    (``accepted`` false); only while the request is in review."""

    changes = LineChanges(
        accepted=body.accepted,
        new_cost=body.new_cost,
        increased_on=body.increased_on,
        regular_discount_pct=body.regular_discount_pct,
        public_price=body.public_price,
        observation=body.observation,
    )
    return _detail(await service.update_line(principal, request_id, line_id, changes))


@router.post(
    "/increase-requests/{request_id}/approve",
    response_model=RequestDetailResponse,
    summary="Approve a request",
)
@inject
async def approve(
    request_id: UUID,
    body: DecisionBody,
    principal: Principal = _principal,
    service: IncreaseService = _service,
) -> RequestDetailResponse:
    """Each accepted line goes to the history of increases, pending in SIESA."""

    return _detail(await service.approve(principal, request_id, body.note))


@router.post(
    "/increase-requests/{request_id}/reject",
    response_model=RequestDetailResponse,
    summary="Reject a request",
)
@inject
async def reject(
    request_id: UUID,
    body: DecisionBody,
    principal: Principal = _principal,
    service: IncreaseService = _service,
) -> RequestDetailResponse:
    """Reject with a reason; nothing reaches the history."""

    return _detail(await service.reject(principal, request_id, body.note))


__all__ = ["router"]
