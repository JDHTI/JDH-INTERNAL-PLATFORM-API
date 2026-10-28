"""Endpoints of the supporting documents of supports.

Mounted at ``/supplier-supports`` before the main router, like the bulk one.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.core.errors import InvalidInputError
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.supplier_supports.application.attachments import SupportAttachmentService
from app.modules.supplier_supports.domain.attachments import (
    Attachment,
    AttachResult,
    FileUpload,
    SelectionPreview,
)
from app.modules.supplier_supports.presentation.router import SearchRequest

router = APIRouter()
_service = Depends(Provide[ApplicationContainer.v1_container.supplier_support_attachment_service])
_principal = Depends(get_current_principal)

_SEARCH_IGNORED = {"page", "size", "sort"}


class SelectionBody(BaseModel):
    """Which supports a document is for: some by id, the ones a search finds, or both."""

    support_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    search: SearchRequest | None = Field(
        default=None,
        description="Same filters as POST /supplier-supports/search (references, clients, "
        "suppliers, state, text, conditions...); page, size and sort are ignored. It needs at "
        "least one filter.",
    )


class AttachmentResponse(BaseModel):
    """A document backing a support."""

    document_id: UUID
    name: str
    original_filename: str
    extension: str
    content_type: str
    size_bytes: int
    uploaded_by: str
    uploaded_at: datetime
    linked_by: str
    linked_at: datetime
    support_count: int = Field(description="How many supports this document backs in all")


class ViewResponse(BaseModel):
    """A temporary link to open a support file."""

    url: str
    filename: str
    name: str
    content_type: str
    extension: str
    size_bytes: int
    inline: bool = Field(description="The link displays the file; false when it saves it")
    expires_in: int = Field(description="Seconds the link stays valid")


class SelectionPreviewResponse(BaseModel):
    """How many supports a selection reaches."""

    total: int
    capped: bool
    limit: int
    support_ids: list[UUID] = Field(description="The first 20, to show a sample")


class AttachResponse(BaseModel):
    """What an upload or link did."""

    documents: list[UUID]
    supports: int
    links_created: int


def _attachment(item: Attachment) -> AttachmentResponse:
    return AttachmentResponse.model_validate(item, from_attributes=True)


def _search(body: SelectionBody) -> dict[str, Any] | None:
    if body.search is None:
        return None
    return body.search.model_dump(exclude=_SEARCH_IGNORED, exclude_unset=True)


def _json(text: str | None, name: str) -> Any:
    if not text or not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidInputError(f"{name} debe ser JSON válido") from exc


def _selection(text: str | None) -> SelectionBody:
    value = _json(text, "selection")
    return SelectionBody.model_validate(value or {})


def _result(result: AttachResult) -> AttachResponse:
    return AttachResponse(
        documents=result.documents, supports=result.supports, links_created=result.links_created
    )


async def _uploads(files: list[UploadFile], limit: int) -> list[FileUpload]:
    # One byte over the limit is enough for the document manager to reject the file.
    return [FileUpload(file.filename, await file.read(limit + 1)) for file in files]


def _tag_list(tags: str | None) -> list[str]:
    return [tag for tag in (tags or "").split(",") if tag.strip()]


@router.post(
    "/attachments/preview",
    response_model=SelectionPreviewResponse,
    summary="How many supports a selection reaches",
)
@inject
async def preview_selection(
    body: SelectionBody,
    principal: Principal = _principal,
    service: SupportAttachmentService = _service,
) -> SelectionPreviewResponse:
    """Resolve a selection (ids and/or a search) without uploading anything."""

    result: SelectionPreview = await service.preview(
        principal, support_ids=body.support_ids, search=_search(body)
    )
    return SelectionPreviewResponse(
        total=result.total, capped=result.capped, limit=result.limit, support_ids=result.supports
    )


@router.post(
    "/attachments",
    response_model=AttachResponse,
    status_code=201,
    summary="Upload support and link it to a selection of supports",
    description=(
        "multipart/form-data. `files`: screenshots, PDFs... (stored once, in the supports "
        "folder of the document manager). `selection`: JSON `{support_ids: [...], search: "
        "{product_skus: [...], supplier_codes: [...]}}`. `document_ids`: JSON list of "
        "documents already in that folder to link too. `name` only with one file; `tags` is "
        "comma-separated. Everything or nothing."
    ),
)
@inject
async def attach(
    files: list[UploadFile] = File(default=[]),  # noqa: B008
    selection: str = Form(),
    document_ids: str | None = Form(default=None),
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
    tags: str | None = Form(default=None),
    principal: Principal = _principal,
    service: SupportAttachmentService = _service,
) -> AttachResponse:
    """Store the files once and link them to every support the selection reaches."""

    chosen = _selection(selection)
    ids = _json(document_ids, "document_ids") or []
    try:
        if not isinstance(ids, list):
            raise ValueError
        linked = [UUID(str(item)) for item in ids]
    except ValueError as exc:
        raise InvalidInputError("document_ids debe ser una lista de identificadores") from exc
    result = await service.attach(
        principal,
        support_ids=chosen.support_ids,
        search=_search(chosen),
        files=await _uploads(files, service.max_file_bytes),
        document_ids=linked,
        name=name,
        description=description,
        tags=_tag_list(tags),
    )
    return _result(result)


@router.post(
    "/{support_id}/attachments",
    response_model=AttachResponse,
    status_code=201,
    summary="Upload support for one support",
)
@inject
async def attach_to_support(
    support_id: UUID,
    files: list[UploadFile] = File(default=[]),  # noqa: B008
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
    tags: str | None = Form(default=None),
    principal: Principal = _principal,
    service: SupportAttachmentService = _service,
) -> AttachResponse:
    """Store the files in the supports folder and link them to this support."""

    result = await service.attach(
        principal,
        support_ids=[support_id],
        files=await _uploads(files, service.max_file_bytes),
        name=name,
        description=description,
        tags=_tag_list(tags),
    )
    return _result(result)


@router.get(
    "/{support_id}/attachments",
    response_model=list[AttachmentResponse],
    summary="Documents backing a support",
)
@inject
async def list_attachments(
    support_id: UUID,
    _: Principal = _principal,
    service: SupportAttachmentService = _service,
) -> list[AttachmentResponse]:
    """The supporting documents of a support; open one with ``.../{document_id}/view``."""

    return [_attachment(item) for item in await service.list(support_id)]


@router.get(
    "/{support_id}/attachments/{document_id}/view",
    response_model=ViewResponse,
    summary="Open a document of a support",
)
@inject
async def view_attachment(
    support_id: UUID,
    document_id: UUID,
    download: bool = Query(default=False, description="Save the file instead of displaying it"),
    principal: Principal = _principal,
    service: SupportAttachmentService = _service,
) -> ViewResponse:
    """A short-lived signed link to display the document (an image, a PDF) in the browser.

    Anyone who can read the support can open its proofs; the document has to be linked to that
    support. The link expires in a few minutes: ask for a new one instead of storing it.
    """

    attachment, link = await service.view(principal, support_id, document_id, download=download)
    return ViewResponse(
        url=link.url,
        filename=link.filename,
        name=attachment.name,
        content_type=attachment.content_type,
        extension=attachment.extension,
        size_bytes=attachment.size_bytes,
        inline=not download,
        expires_in=link.expires_in,
    )


@router.delete(
    "/{support_id}/attachments/{document_id}",
    status_code=204,
    summary="Take a document off a support",
)
@inject
async def unlink_attachment(
    support_id: UUID,
    document_id: UUID,
    principal: Principal = _principal,
    service: SupportAttachmentService = _service,
) -> Response:
    """Unlink only: the document stays in the document manager and on its other supports."""

    await service.unlink(principal, support_id, document_id)
    return Response(status_code=204)


__all__ = ["router"]
