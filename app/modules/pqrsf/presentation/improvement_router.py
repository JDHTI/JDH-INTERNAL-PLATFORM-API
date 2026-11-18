"""Improvement opportunity (OM, P.06): its template, its consecutive and the OMs of a case."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from urllib.parse import quote
from uuid import UUID
from zoneinfo import ZoneInfo

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.core.errors import InvalidInputError
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.pqrsf.application.improvement_opportunities import (
    EDITED_MAX_BYTES,
    ImprovementOpportunityService,
    OmCommand,
    OmFile,
)
from app.modules.pqrsf.domain.improvement import (
    TEMPLATE_MAX_BYTES,
    DocumentTemplate,
    ImprovementOpportunity,
    OmFilters,
)

router = APIRouter(prefix="/pqrsf")
_LOCAL = ZoneInfo("America/Bogota")
_READ_GATE = Depends(require_permission("pqrsf"))
_oms = Depends(Provide[ApplicationContainer.v1_container.pqrsf_om_service])
_principal = Depends(get_current_principal)
_FILE_BODY = {
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {
                    "file": {"type": "string", "format": "binary"},
                    "version_label": {"type": "string", "maxLength": 60},
                },
            }
        }
    }
}


class TemplateResponse(BaseModel):
    """A template version kept in the bucket."""

    id: UUID
    kind: str
    version_label: str
    filename: str
    size_bytes: int
    checksum_sha256: str
    is_active: bool
    uploaded_by: UUID | None
    created_at: datetime | None


class OmSettingsRequest(BaseModel):
    """Where the OM consecutive goes on: Calidad's last number plus one."""

    next_number: int = Field(ge=1, le=9_999_999)


class OmSettingsResponse(BaseModel):
    """The number the next OM takes."""

    next_number: int


class SignerResponse(BaseModel):
    """Who signs the OM."""

    name: str | None
    position: str | None
    email: str | None
    phone: str | None


class OmRequest(BaseModel):
    """What the team writes on the OM; the rest comes from the case."""

    supplier_code: str | None = Field(default=None, max_length=40)
    supplier_name: str | None = Field(default=None, max_length=200)
    immediate_actions: str | None = Field(default=None, max_length=2_000)
    request_note: str | None = Field(default=None, max_length=1_000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=6)
    document_number: str | None = Field(default=None, max_length=200)
    document_date: date | None = None


class OmResponse(BaseModel):
    """An OM of a case: its data, its documents and whether it went to the supplier."""

    id: UUID
    case_id: UUID
    number: int
    code: str
    supplier_code: str | None
    supplier_name: str | None
    immediate_actions: str | None
    request_note: str | None
    document_number: str | None
    document_date: date | None
    evidence_ids: list[UUID]
    signer: SignerResponse | None
    has_docx: bool
    has_pdf: bool
    edited: bool
    status: Literal["draft", "sent"]
    generated_at: datetime | None
    sent_at: datetime | None
    created_at: datetime | None


def _template(value: DocumentTemplate) -> TemplateResponse:
    return TemplateResponse.model_validate(value, from_attributes=True)


def _om(value: ImprovementOpportunity) -> OmResponse:
    signer = value.signer
    return OmResponse(
        id=value.id,
        case_id=value.case_id,
        number=value.number,
        code=value.code,
        supplier_code=value.supplier_code,
        supplier_name=value.supplier_name,
        immediate_actions=value.immediate_actions,
        request_note=value.request_note,
        document_number=value.document_number,
        document_date=value.document_date,
        evidence_ids=value.evidence_ids,
        signer=SignerResponse(
            name=signer.name, position=signer.position, email=signer.email, phone=signer.phone
        )
        if signer
        else None,
        has_docx=value.docx_key is not None,
        has_pdf=value.pdf_key is not None,
        edited=value.edited,
        status=value.status,
        generated_at=value.generated_at,
        sent_at=value.sent_at,
        created_at=value.created_at,
    )


def _file(value: OmFile, inline: bool = False) -> Response:
    disposition = "inline" if inline else "attachment"
    return Response(
        content=value.content,
        media_type=value.content_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(value.filename)}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _upload(request: Request, limit: int) -> tuple[str, bytes, str]:
    """The file and the version label of a multipart upload."""

    form = await request.form(max_files=1, max_fields=5)
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise InvalidInputError("Adjunta el archivo en el campo «file»")
    content = await upload.read(limit + 1)
    if len(content) > limit:
        raise InvalidInputError(f"El archivo no puede superar {limit // (1024 * 1024)} MB")
    return upload.filename or "documento.docx", content, str(form.get("version_label") or "")


# --- Template and consecutive (managers) ---


@router.get(
    "/templates/{kind}",
    response_model=list[TemplateResponse],
    summary="List the versions of a document template",
    dependencies=[_READ_GATE],
)
@inject
async def list_templates(
    kind: Literal["om"],
    principal: Principal = _principal,
    service: ImprovementOpportunityService = _oms,
) -> list[TemplateResponse]:
    """Every version of the template, newest first; ``is_active`` marks the one in use."""

    return [_template(t) for t in await service.list_templates(kind, principal)]


@router.post(
    "/templates/{kind}",
    response_model=TemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a new version of a document template",
    dependencies=[_READ_GATE],
    openapi_extra={"requestBody": _FILE_BODY},
)
@inject
async def upload_template(
    kind: Literal["om"],
    request: Request,
    principal: Principal = _principal,
    service: ImprovementOpportunityService = _oms,
) -> TemplateResponse:
    """A Word with the ``{{ … }}`` marks of the OM; it becomes the active version.

    It is checked first: every mark of the format must be there, none unknown, and it must
    fill with sample values. The previous versions stay in the history.
    """

    filename, content, label = await _upload(request, TEMPLATE_MAX_BYTES)
    return _template(await service.upload_template(kind, filename, content, label, principal))


@router.get(
    "/templates/{kind}/{template_id}/download",
    summary="Download a version of a document template",
    dependencies=[_READ_GATE],
)
@inject
async def download_template(
    kind: Literal["om"],
    template_id: UUID,
    principal: Principal = _principal,
    service: ImprovementOpportunityService = _oms,
) -> Response:
    """The Word of a template version."""

    return _file(await service.template_file(template_id, principal))


@router.get(
    "/om/settings",
    response_model=OmSettingsResponse,
    summary="The OM consecutive",
    dependencies=[_READ_GATE],
)
@inject
async def get_om_settings(
    principal: Principal = _principal,
    service: ImprovementOpportunityService = _oms,
) -> OmSettingsResponse:
    """The number the next OM takes (managers)."""

    return OmSettingsResponse(next_number=await service.next_number(principal))


@router.put(
    "/om/settings",
    response_model=OmSettingsResponse,
    summary="Set where the OM consecutive goes on",
    dependencies=[_READ_GATE],
)
@inject
async def set_om_settings(
    payload: OmSettingsRequest,
    principal: Principal = _principal,
    service: ImprovementOpportunityService = _oms,
) -> OmSettingsResponse:
    """Calidad's last OM number plus one; it must be above every OM already generated."""

    return OmSettingsResponse(
        next_number=await service.set_next_number(payload.next_number, principal)
    )


# --- History (follow-up across cases) ---


class OmHistoryItemResponse(OmResponse):
    """An OM of the history, with its case and whether the supplier answered."""

    case_code: str
    case_status: str
    case_closed: bool
    customer_name: str | None
    site_name: str | None
    sent_to: list[str]
    replied_at: datetime | None
    # Days since it was sent with no answer yet (``None`` when answered or not sent).
    days_waiting: int | None


class OmHistoryResponse(BaseModel):
    """A page of the OM history and its totals by state."""

    items: list[OmHistoryItemResponse]
    total: int
    page: int
    size: int
    drafts: int
    awaiting_reply: int
    replied: int


@router.get(
    "/improvement-opportunities",
    response_model=OmHistoryResponse,
    summary="History of the improvement opportunities (all cases)",
    dependencies=[_READ_GATE],
)
@inject
async def om_history(
    status_filter: Literal["draft", "sent"] | None = Query(default=None, alias="status"),
    site_id: UUID | None = None,
    supplier: str | None = Query(default=None, max_length=100),
    q: str | None = Query(default=None, max_length=100),
    replied: bool | None = None,
    sent_from: date | None = None,
    sent_to: date | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=25, ge=1, le=100),
    principal: Principal = _principal,
    service: ImprovementOpportunityService = _oms,
) -> OmHistoryResponse:
    """Every OM, newest sent first, to follow up the suppliers (who reads PQRSF).

    ``replied=false`` lists the sent OMs still waiting for the supplier; a reply is the
    first email on the case, after the OM was sent, from one of its recipients.
    """

    result = await service.history(
        OmFilters(
            status=status_filter,
            site_id=site_id,
            supplier=supplier.strip() if supplier and supplier.strip() else None,
            query=q.strip() if q and q.strip() else None,
            replied=replied,
            sent_from=sent_from,
            sent_to=sent_to,
        ),
        page,
        size,
        principal,
    )
    today = datetime.now(_LOCAL).date()
    items = []
    for row in result.rows:
        waiting = (
            (today - row.om.sent_at.astimezone(_LOCAL).date()).days
            if row.om.sent_at and row.replied_at is None
            else None
        )
        items.append(
            OmHistoryItemResponse(
                **_om(row.om).model_dump(),
                case_code=row.case_code,
                case_status=row.case_status,
                case_closed=row.case_closed,
                customer_name=row.customer_name,
                site_name=row.site_name,
                sent_to=list(row.sent_to),
                replied_at=row.replied_at,
                days_waiting=waiting,
            )
        )
    return OmHistoryResponse(
        items=items,
        total=result.total,
        page=page,
        size=size,
        drafts=result.drafts,
        awaiting_reply=result.awaiting_reply,
        replied=result.replied,
    )


# --- OMs of a case ---

_CASE_OMS = "/cases/{case_id}/improvement-opportunities"


@router.get(_CASE_OMS, response_model=list[OmResponse], summary="List the OMs of a case")
@inject
async def list_case_oms(
    case_id: UUID,
    principal: Principal = _principal,
    service: ImprovementOpportunityService = _oms,
) -> list[OmResponse]:
    """The OMs of the case, one per supplier, for anyone who can see it."""

    return [_om(om) for om in await service.list_for_case(case_id, principal)]


@router.post(
    _CASE_OMS,
    response_model=OmResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate the OM of a case for a supplier",
)
@inject
async def generate_om(
    case_id: UUID,
    payload: OmRequest,
    principal: Principal = _principal,
    service: ImprovementOpportunityService = _oms,
) -> OmResponse:
    """Fill the P.06 from the case (or fill it again, keeping its number).

    Only who works the open case. The site needs its OM code and there must be an active
    template. ``evidence_ids`` are photos of the client's request printed on the OM (up to
    6). ``has_pdf`` is false when the converter was down: retry with ``…/pdf``.
    """

    om = await service.generate(
        case_id,
        OmCommand(
            supplier_code=payload.supplier_code,
            supplier_name=payload.supplier_name,
            immediate_actions=payload.immediate_actions,
            request_note=payload.request_note,
            evidence_ids=tuple(payload.evidence_ids),
            document_number=payload.document_number,
            document_date=payload.document_date,
        ),
        principal,
    )
    return _om(om)


@router.get(
    f"{_CASE_OMS}/{{om_id}}/document",
    summary="Download the Word or the PDF of an OM",
)
@inject
async def download_om(
    case_id: UUID,
    om_id: UUID,
    format: Literal["docx", "pdf"] = "pdf",  # noqa: A002 - the query name the UI uses
    inline: bool = False,
    principal: Principal = _principal,
    service: ImprovementOpportunityService = _oms,
) -> Response:
    """The OM as PDF (``inline=true`` to show it in the browser) or as editable Word."""

    return _file(await service.document(case_id, om_id, format, principal), inline=inline)


@router.put(
    f"{_CASE_OMS}/{{om_id}}/document",
    response_model=OmResponse,
    summary="Upload a hand-edited Word of an OM",
    openapi_extra={"requestBody": _FILE_BODY},
)
@inject
async def upload_edited_om(
    case_id: UUID,
    om_id: UUID,
    request: Request,
    principal: Principal = _principal,
    service: ImprovementOpportunityService = _oms,
) -> OmResponse:
    """Replace the Word of a draft OM; its PDF is made again from it."""

    filename, content, _ = await _upload(request, EDITED_MAX_BYTES)
    return _om(await service.upload_edited(case_id, om_id, filename, content, principal))


@router.post(
    f"{_CASE_OMS}/{{om_id}}/pdf",
    response_model=OmResponse,
    summary="Convert an OM to PDF again",
)
@inject
async def retry_om_pdf(
    case_id: UUID,
    om_id: UUID,
    principal: Principal = _principal,
    service: ImprovementOpportunityService = _oms,
) -> OmResponse:
    """After the converter was down, make the PDF from the stored Word."""

    return _om(await service.retry_pdf(case_id, om_id, principal))


__all__ = ["router"]
