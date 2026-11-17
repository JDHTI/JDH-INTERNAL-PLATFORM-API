"""Messages to suppliers written from a case, and the supplier contacts already used."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel

from app.app_container import ApplicationContainer
from app.core.errors import InvalidInputError
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.pqrsf.application.comments import UploadedFile
from app.modules.pqrsf.application.supplier_messages import SupplierMessageService
from app.modules.pqrsf.domain.notifications import Notification
from app.modules.pqrsf.presentation.tracking_router import (
    CaseEventResponse,
    CaseFileResponse,
    RejectedFileResponse,
    _event,
)

router = APIRouter(prefix="/pqrsf")
_READ_GATE = Depends(require_permission("pqrsf"))
_messages = Depends(Provide[ApplicationContainer.v1_container.pqrsf_supplier_message_service])
_principal = Depends(get_current_principal)

_MAX_FILES = 20
_TRUE = {"1", "true", "yes", "on"}
_BODY = {
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["kind", "to", "body"],
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["request", "response", "improvement", "notice"],
                    },
                    "to": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                    "cc": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                    "body": {"type": "string", "maxLength": 5000},
                    "supplier_code": {"type": "string"},
                    "supplier_name": {"type": "string"},
                    "include_evidence": {"type": "boolean", "default": False},
                    "include_case_data": {"type": "boolean", "default": True},
                    "om_id": {"type": "string", "format": "uuid"},
                    "files": {"type": "array", "items": {"type": "string", "format": "binary"}},
                },
            }
        }
    }
}


class SupplierEmailResponse(BaseModel):
    """One email of a supplier message and where its delivery stands."""

    id: UUID
    event_id: UUID | None
    to: str
    cc: list[str] = []
    subject: str
    status: Literal["pending", "sent", "failed"]
    attempts: int
    error: str | None
    sent_at: datetime | None
    created_at: datetime | None


class SupplierMessageResponse(CaseEventResponse):
    """The new timeline entry, its files and one queued email per recipient."""

    files: list[CaseFileResponse] = []
    rejected_files: list[RejectedFileResponse] = []
    emails: list[SupplierEmailResponse] = []


class SupplierContactResponse(BaseModel):
    """An address already used for a supplier."""

    id: UUID
    supplier_code: str | None
    supplier_name: str | None
    email: str
    contact_name: str | None
    last_used_at: datetime | None


def _uuid(value: object) -> UUID | None:
    """An optional identifier from a form field; a malformed one is a validation error."""

    text = str(value or "").strip()
    if not text:
        return None
    try:
        return UUID(text)
    except ValueError as exc:
        raise InvalidInputError("El identificador de la OM no es válido") from exc


def _email(item: Notification) -> SupplierEmailResponse:
    event_id = item.payload.get("event_id")
    return SupplierEmailResponse(
        id=item.id,
        event_id=UUID(event_id) if event_id else None,
        to=item.recipient_email,
        cc=[str(address) for address in item.payload.get("cc") or []],
        subject=item.subject,
        status=item.status,
        attempts=item.attempts,
        error=item.error,
        sent_at=item.sent_at,
        created_at=item.created_at,
    )


@router.post(
    "/cases/{case_id}/supplier-messages",
    response_model=SupplierMessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Write to the supplier from a case",
    openapi_extra={"requestBody": _BODY},
)
@inject
async def send_supplier_message(
    case_id: UUID,
    request: Request,
    principal: Principal = _principal,
    service: SupplierMessageService = _messages,
) -> SupplierMessageResponse:
    """Email the supplier from the PQRSF mailbox and record it in the timeline.

    Multipart form: ``kind``, ``to`` (repeated, up to 5), optional ``cc`` (repeated, up to 5
    people of the organization, copied on the first email), ``body``, optional
    ``supplier_code`` and ``supplier_name``, ``include_evidence`` (attach the client's
    photos and video), ``include_case_data`` (client, site and products; on by default)
    and ``files``. Only who works the case, and only while it is open. With ``om_id``
    (``kind=improvement``) the PDF of that OM goes attached and the OM is marked as sent.
    """

    form = await request.form(max_files=_MAX_FILES, max_fields=30)
    max_bytes = service.files.guard.max_attachment_bytes
    uploads = [
        UploadedFile(upload.filename or "archivo", await upload.read(max_bytes + 1))
        for upload in form.getlist("files")
        if hasattr(upload, "read")
    ]
    result = await service.send(
        case_id,
        principal,
        kind=str(form.get("kind") or ""),
        to=[str(value) for value in form.getlist("to")],
        cc=[str(value) for value in form.getlist("cc")],
        body=str(form.get("body") or ""),
        supplier_code=str(form.get("supplier_code") or "") or None,
        supplier_name=str(form.get("supplier_name") or "") or None,
        files=uploads,
        include_evidence=str(form.get("include_evidence") or "").lower() in _TRUE,
        include_case_data=str(form.get("include_case_data") or "true").lower() in _TRUE,
        om_id=_uuid(form.get("om_id")),
    )
    return SupplierMessageResponse(
        **_event(result.event).model_dump(),
        files=[
            CaseFileResponse(
                id=item.id,
                filename=item.original_filename,
                content_type=item.content_type,
                size_bytes=item.size_bytes,
                created_at=item.created_at,
                comment_event_id=item.comment_event_id,
            )
            for item in result.files
        ],
        rejected_files=[RejectedFileResponse(**item) for item in result.rejected],
        emails=[_email(item) for item in result.emails],
    )


@router.get(
    "/cases/{case_id}/supplier-messages",
    response_model=list[SupplierEmailResponse],
    summary="List the emails sent to suppliers from a case",
)
@inject
async def list_supplier_messages(
    case_id: UUID,
    principal: Principal = _principal,
    service: SupplierMessageService = _messages,
) -> list[SupplierEmailResponse]:
    """One entry per recipient of each message, with its delivery state."""

    return [_email(item) for item in await service.list_sent(case_id, principal)]


@router.post(
    "/cases/{case_id}/supplier-messages/{notification_id}/retry",
    response_model=SupplierEmailResponse,
    summary="Retry a failed email to a supplier",
)
@inject
async def retry_supplier_message(
    case_id: UUID,
    notification_id: UUID,
    principal: Principal = _principal,
    service: SupplierMessageService = _messages,
) -> SupplierEmailResponse:
    """Send an email to a supplier again after its delivery gave up."""

    return _email(await service.retry(case_id, notification_id, principal))


@router.get(
    "/supplier-contacts",
    dependencies=[_READ_GATE],
    response_model=list[SupplierContactResponse],
    summary="Supplier addresses already used",
)
@inject
async def list_supplier_contacts(
    supplier_code: str | None = Query(default=None, max_length=40),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=10, ge=1, le=50),
    principal: Principal = _principal,
    service: SupplierMessageService = _messages,
) -> list[SupplierContactResponse]:
    """Addresses the cases wrote to for a supplier, most recently used first."""

    return [
        SupplierContactResponse(
            id=item.id,
            supplier_code=item.supplier_code or None,
            supplier_name=item.supplier_name,
            email=item.email,
            contact_name=item.contact_name,
            last_used_at=item.last_used_at,
        )
        for item in await service.contacts_for(supplier_code, q, limit)
    ]
