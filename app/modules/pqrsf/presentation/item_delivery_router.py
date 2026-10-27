"""Answer a document requested in a case with a file, and send it to the client."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ValidationError

from app.app_container import ApplicationContainer
from app.core.errors import InvalidInputError
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.pqrsf.application.item_delivery import ItemDeliveryService
from app.modules.pqrsf.domain.item_delivery import ItemDelivery
from app.modules.pqrsf.presentation.certificates_router import (
    ManagerDocumentResponse,
    _document,
)
from app.modules.pqrsf.presentation.tracking_router import CaseResponse, _case

router = APIRouter(prefix="/pqrsf/cases")
_service = Depends(Provide[ApplicationContainer.v1_container.pqrsf_item_delivery_service])
_principal = Depends(get_current_principal)

_TRUE = {"1", "true", "yes", "on"}


class DeliveryRequest(BaseModel):
    """Answer a document with a file of the document manager."""

    source: Literal["vault", "upload"] = "vault"
    document_id: UUID | None = None
    send_to_client: bool = True
    delay_cause: str | None = None
    delay_detail: str | None = None


class DeliveryEmailResponse(BaseModel):
    """Where the email with the file stands."""

    status: Literal["not_sent", "pending", "sent", "failed"]
    sent_at: datetime | None
    error: str | None


class ItemDeliveryResponse(BaseModel):
    """The answer given to a requested document."""

    id: UUID
    item_id: UUID
    source: Literal["vault", "upload"]
    filename: str
    # Vault: the file of the manager (and where it is kept). Upload: the file of the case.
    document_id: UUID | None
    document_path: str | None
    case_file_id: UUID | None
    email: DeliveryEmailResponse
    created_at: datetime


class DeliverResponse(BaseModel):
    """The case after the answer, and the answer."""

    case: CaseResponse
    delivery: ItemDeliveryResponse


_BODY = {
    "required": True,
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "source": {"enum": ["vault"]},
                    "document_id": {"type": "string", "format": "uuid"},
                    "send_to_client": {"type": "boolean", "default": True},
                    "delay_cause": {"type": "string"},
                    "delay_detail": {"type": "string"},
                },
            }
        },
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "properties": {
                    "source": {"enum": ["vault", "upload"]},
                    "document_id": {"type": "string", "format": "uuid"},
                    "file": {"type": "string", "format": "binary"},
                    "send_to_client": {"type": "boolean", "default": True},
                    "delay_cause": {"type": "string"},
                    "delay_detail": {"type": "string"},
                },
            }
        },
    },
}


def _delivery(value: ItemDelivery) -> ItemDeliveryResponse:
    return ItemDeliveryResponse(
        id=value.id,
        item_id=value.item_id,
        source=value.source,
        filename=value.filename,
        document_id=value.document_id,
        document_path=value.document_path,
        case_file_id=value.attachment_id,
        email=DeliveryEmailResponse(
            status=value.email_status,
            sent_at=value.email_sent_at,
            error=value.email_error,
        ),
        created_at=value.created_at,
    )


async def _read(
    request: Request, max_file_bytes: int
) -> tuple[DeliveryRequest, str | None, bytes | None]:
    """Read the answer sent as JSON (a manager file) or as a multipart form (with a file)."""

    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form(max_files=1, max_fields=20)
        upload = form.get("file")
        content = await upload.read(max_file_bytes + 1) if hasattr(upload, "read") else None
        text = lambda key: (str(form.get(key)).strip() or None) if form.get(key) else None  # noqa: E731
        try:
            body = DeliveryRequest(
                source=text("source") or ("upload" if content else "vault"),
                document_id=text("document_id"),
                send_to_client=(text("send_to_client") or "true").lower() in _TRUE,
                delay_cause=text("delay_cause"),
                delay_detail=text("delay_detail"),
            )
        except ValidationError as exc:
            raise InvalidInputError("Los datos de la respuesta no son válidos") from exc
        return body, getattr(upload, "filename", None), content
    try:
        return DeliveryRequest.model_validate_json(await request.body()), None, None
    except ValidationError as exc:
        raise InvalidInputError("Los datos de la respuesta no son válidos") from exc


@router.post(
    "/{case_id}/items/{item_id}/delivery",
    response_model=DeliverResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Answer a requested document with a file",
    openapi_extra={"requestBody": _BODY},
)
@inject
async def deliver_item(
    case_id: UUID,
    item_id: UUID,
    request: Request,
    principal: Principal = _principal,
    service: ItemDeliveryService = _service,
) -> DeliverResponse:
    """Register the response of a document with its file and email it to the client.

    Two ways: ``vault`` links a file of the quality area of the document manager (JSON
    ``{source, document_id}``); ``upload`` sends a file once, stored with the case and never in
    the manager (multipart ``source=upload`` and ``file``), only for documents whose type
    allows it and free-text ones. ``send_to_client`` (default true) emails the file from the
    PQRSF mailbox. A late answer needs ``delay_cause``. The case must be open and the document
    unanswered. ``email.status`` says where the email stands.
    """

    body, filename, content = await _read(request, service.guard.max_attachment_bytes)
    result = await service.deliver(
        case_id,
        item_id,
        principal,
        source=body.source,
        document_id=body.document_id,
        filename=filename,
        content=content,
        send_to_client=body.send_to_client,
        delay_cause=body.delay_cause,
        delay_detail=body.delay_detail,
    )
    return DeliverResponse(case=_case(result.view), delivery=_delivery(result.delivery))


@router.get(
    "/{case_id}/deliveries",
    response_model=list[ItemDeliveryResponse],
    summary="Answers given to the requested documents",
)
@inject
async def list_deliveries(
    case_id: UUID, principal: Principal = _principal, service: ItemDeliveryService = _service
) -> list[ItemDeliveryResponse]:
    """The files that answered the documents of the case, with where their email stands."""

    return [_delivery(item) for item in await service.list_for_case(case_id, principal)]


@router.get(
    "/{case_id}/documents/search",
    response_model=list[ManagerDocumentResponse],
    summary="Files of the quality document manager to answer with",
)
@inject
async def search_documents(
    case_id: UUID,
    q: str | None = Query(default=None, max_length=100),  # noqa: B008
    folder_id: UUID | None = None,
    principal: Principal = _principal,
    service: ItemDeliveryService = _service,
) -> list[ManagerDocumentResponse]:
    """Files of the quality area (at most 100), for whoever can work the case."""

    return [_document(d) for d in await service.search_documents(case_id, principal, q, folder_id)]


__all__ = ["router"]
