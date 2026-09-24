"""HTTP endpoints of the per-area document manager.

``area_router`` is mounted under ``/areas`` (folders and uploads live inside an
area); ``router`` is mounted under ``/documents`` (search and operations on
one document, whose area is resolved from the document itself).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.modules.documents.application.dto import (
    DocumentPage,
    FolderView,
    SearchQuery,
    UpdateDocumentCommand,
    UploadCommand,
)
from app.modules.documents.application.service import DocumentService
from app.modules.documents.domain.entities import Document
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal

area_router = APIRouter()
router = APIRouter()

_principal = Depends(get_current_principal)
_service = Depends(Provide[ApplicationContainer.v1_container.document_service])


# --- Schemas ---


class FolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=1000)


class FolderUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=1000)


class FolderResponse(BaseModel):
    id: UUID
    area_id: UUID
    name: str
    description: str | None
    created_by: str
    document_count: int
    created_at: datetime
    updated_at: datetime


class DocumentResponse(BaseModel):
    """``storage_key`` is not exposed: bytes only leave through signed links."""

    id: UUID
    area_id: UUID
    folder_id: UUID
    name: str
    original_filename: str
    extension: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    description: str | None
    tags: list[str]
    uploaded_by: str
    created_at: datetime
    updated_at: datetime


class DocumentPageResponse(BaseModel):
    items: list[DocumentResponse]
    total: int
    page: int
    size: int
    pages: int


class DocumentUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = None


class DownloadLinkResponse(BaseModel):
    url: str
    filename: str
    expires_in: int


def _folder(view: FolderView) -> FolderResponse:
    return FolderResponse(
        **{
            name: getattr(view.folder, name)
            for name in FolderResponse.model_fields
            if name != "document_count"
        },
        document_count=view.document_count,
    )


def _document(document: Document) -> DocumentResponse:
    return DocumentResponse.model_validate(document, from_attributes=True)


def _page(page: DocumentPage) -> DocumentPageResponse:
    return DocumentPageResponse(
        items=[_document(d) for d in page.items],
        total=page.total,
        page=page.page,
        size=page.size,
        pages=page.pages,
    )


# --- Inside an area ---


@area_router.get(
    "/{slug}/folders", response_model=list[FolderResponse], summary="Folders of an area"
)
@inject
async def list_folders(
    slug: str, principal: Principal = _principal, service: DocumentService = _service
) -> list[FolderResponse]:
    return [_folder(v) for v in await service.list_folders(slug, principal)]


@area_router.post(
    "/{slug}/folders",
    response_model=FolderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create folder",
)
@inject
async def create_folder(
    slug: str,
    payload: FolderRequest,
    principal: Principal = _principal,
    service: DocumentService = _service,
) -> FolderResponse:
    return _folder(await service.create_folder(slug, payload.name, payload.description, principal))


@area_router.patch(
    "/{slug}/folders/{folder_id}", response_model=FolderResponse, summary="Update folder"
)
@inject
async def update_folder(
    slug: str,
    folder_id: UUID,
    payload: FolderUpdateRequest,
    principal: Principal = _principal,
    service: DocumentService = _service,
) -> FolderResponse:
    view = await service.update_folder(
        slug,
        folder_id,
        frozenset(payload.model_fields_set),
        payload.name,
        payload.description,
        principal,
    )
    return _folder(view)


@area_router.delete(
    "/{slug}/folders/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete empty folder",
)
@inject
async def delete_folder(
    slug: str,
    folder_id: UUID,
    principal: Principal = _principal,
    service: DocumentService = _service,
) -> None:
    await service.delete_folder(slug, folder_id, principal)


@area_router.post(
    "/{slug}/folders/{folder_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload document",
    description=(
        "multipart/form-data. `name` defaults to the file name; `tags` is comma-separated."
    ),
)
@inject
async def upload_document(
    slug: str,
    folder_id: UUID,
    file: UploadFile = File(...),  # noqa: B008
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
    tags: str | None = Form(default=None),
    principal: Principal = _principal,
    service: DocumentService = _service,
) -> DocumentResponse:
    # One byte over the limit is enough for the service to reject the file.
    content = await file.read(service.max_upload_bytes + 1)
    command = UploadCommand(
        filename=file.filename, content=content, name=name, description=description, tags=tags
    )
    return _document(await service.upload(slug, folder_id, command, principal))


# --- Across areas / one document ---


@router.get("/search", response_model=DocumentPageResponse, summary="Search documents")
@inject
async def search_documents(
    area: str | None = Query(default=None, description="Area slug; omit to search all yours"),
    folder_id: UUID | None = None,
    q: str | None = Query(default=None, description="Text in name, file name or description"),
    tags: str | None = Query(default=None, description="Comma-separated; all must match"),
    extension: str | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    principal: Principal = _principal,
    service: DocumentService = _service,
) -> DocumentPageResponse:
    query = SearchQuery(
        area_slug=area,
        folder_id=folder_id,
        text=q,
        tags=tags,
        extension=extension,
        page=page,
        size=size,
    )
    return _page(await service.search(query, principal))


@router.get("/{document_id}", response_model=DocumentResponse, summary="Document detail")
@inject
async def get_document(
    document_id: UUID, principal: Principal = _principal, service: DocumentService = _service
) -> DocumentResponse:
    return _document(await service.get_document(document_id, principal))


@router.patch("/{document_id}", response_model=DocumentResponse, summary="Update metadata")
@inject
async def update_document(
    document_id: UUID,
    payload: DocumentUpdateRequest,
    principal: Principal = _principal,
    service: DocumentService = _service,
) -> DocumentResponse:
    command = UpdateDocumentCommand(
        fields=frozenset(payload.model_fields_set), **payload.model_dump()
    )
    return _document(await service.update_document(document_id, command, principal))


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete document")
@inject
async def delete_document(
    document_id: UUID, principal: Principal = _principal, service: DocumentService = _service
) -> None:
    await service.delete_document(document_id, principal)


@router.get(
    "/{document_id}/download-url",
    response_model=DownloadLinkResponse,
    summary="Temporary download link",
)
@inject
async def download_url(
    document_id: UUID, principal: Principal = _principal, service: DocumentService = _service
) -> DownloadLinkResponse:
    link = await service.download_link(document_id, principal)
    return DownloadLinkResponse(url=link.url, filename=link.filename, expires_in=link.expires_in)


__all__ = ["area_router", "router"]
