"""Public PQRSF form and protected administrator endpoints."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile, status
from pydantic import BaseModel, EmailStr

from app.app_container import ApplicationContainer
from app.core.rate_limit import rate_limit
from app.modules.identity.infrastructure.models import UserModel
from app.modules.identity.presentation.router import require_admin
from app.modules.pqrsf.application.service import PqrsfService
from app.modules.pqrsf.domain.entities import (
    ImageUpload,
    PqrsfFilters,
    PqrsfPage,
    PqrsfRequest,
    RequestType,
)
from app.modules.sites.domain.entities import Site

public_router = APIRouter(prefix="/public/pqrsf")
admin_router = APIRouter(prefix="/pqrsf", dependencies=[Depends(rate_limit("medium"))])
_service = Depends(Provide[ApplicationContainer.v1_container.pqrsf_service])


class RequestTypeResponse(BaseModel):
    """Public type catalog item."""

    id: UUID
    code: str
    name: str
    allows_reason: bool
    allows_attachments: bool


class PqrsfSiteResponse(BaseModel):
    """Public site catalog item."""

    id: UUID
    code: str
    name: str


class PqrsfCatalogResponse(BaseModel):
    """Catalog payload used by the isolated public form."""

    request_types: list[RequestTypeResponse]
    sites: list[PqrsfSiteResponse]


class PqrsfReceiptResponse(BaseModel):
    """Minimal public acknowledgement."""

    id: UUID
    reference: str
    created_at: datetime


class PqrsfRequestResponse(BaseModel):
    """Full administrator request representation."""

    id: UUID
    reference: str
    request_type: RequestTypeResponse
    site: PqrsfSiteResponse
    full_name: str
    business_name: str
    nit: str
    address: str
    department: str
    city: str
    email: EmailStr
    reason: str | None
    description: str
    attachments: list[PqrsfAttachmentResponse]
    created_at: datetime
    updated_at: datetime


class PqrsfAttachmentResponse(BaseModel):
    """Administrator attachment metadata without the private storage key."""

    id: UUID
    original_filename: str
    extension: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    created_at: datetime


PqrsfRequestResponse.model_rebuild()


class PqrsfPageResponse(BaseModel):
    """Administrator page response."""

    items: list[PqrsfRequestResponse]
    total: int
    page: int
    size: int


def _type(value: RequestType) -> RequestTypeResponse:
    return RequestTypeResponse.model_validate(value, from_attributes=True)


def _site(value: Site) -> PqrsfSiteResponse:
    return PqrsfSiteResponse.model_validate(value, from_attributes=True)


def _request(value: PqrsfRequest) -> PqrsfRequestResponse:
    return PqrsfRequestResponse(
        id=value.id,
        reference=value.reference,
        request_type=_type(value.request_type),
        site=_site(value.site),
        full_name=value.full_name,
        business_name=value.business_name,
        nit=value.nit,
        address=value.address,
        department=value.department,
        city=value.city,
        email=value.email,
        reason=value.reason,
        description=value.description,
        attachments=[
            PqrsfAttachmentResponse.model_validate(attachment, from_attributes=True)
            for attachment in value.attachments
        ],
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


@public_router.get(
    "/catalogs",
    response_model=PqrsfCatalogResponse,
    summary="Public PQRSF catalogs",
    dependencies=[Depends(rate_limit("relaxed"))],
)
@inject
async def public_catalog(service: PqrsfService = _service) -> PqrsfCatalogResponse:
    """Return active types and sites for the public form."""

    request_types, sites = await service.catalog()
    return PqrsfCatalogResponse(
        request_types=[_type(value) for value in request_types],
        sites=[_site(value) for value in sites],
    )


@public_router.post(
    "/requests",
    response_model=PqrsfReceiptResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a public PQRSF request",
    dependencies=[Depends(rate_limit("strict"))],
)
@inject
async def create_public_request(
    request_type_id: UUID = Form(...),  # noqa: B008
    site_id: UUID = Form(...),  # noqa: B008
    full_name: str = Form(...),  # noqa: B008
    business_name: str = Form(...),  # noqa: B008
    nit: str = Form(...),  # noqa: B008
    address: str = Form(...),  # noqa: B008
    department: str = Form(...),  # noqa: B008
    city: str = Form(...),  # noqa: B008
    email: EmailStr = Form(...),  # noqa: B008
    description: str = Form(...),  # noqa: B008
    reason: str | None = Form(default=None),  # noqa: B008
    images: list[UploadFile] = File(default=[]),  # noqa: B008
    idempotency_key: str = Header(alias="Idempotency-Key"),
    service: PqrsfService = _service,
) -> PqrsfReceiptResponse:
    """Create a request without requiring an Entra session.

    The endpoint accepts multipart form fields and optional images.
    """

    values = {
        "request_type_id": str(request_type_id),
        "site_id": str(site_id),
        "full_name": full_name,
        "business_name": business_name,
        "nit": nit,
        "address": address,
        "department": department,
        "city": city,
        "email": str(email),
        "reason": reason,
        "description": description,
    }
    result = await service.create_request(
        request_type_id=request_type_id,
        site_id=site_id,
        full_name=full_name,
        business_name=business_name,
        nit=nit,
        address=address,
        department=department,
        city=city,
        email=str(email),
        reason=reason,
        description=description,
        idempotency_key=idempotency_key,
        idempotency_payload=values,
        images=[
            ImageUpload(
                filename=image.filename,
                content_type=image.content_type,
                content=await image.read(service.max_attachment_bytes + 1),
            )
            for image in images
        ],
    )
    return PqrsfReceiptResponse(
        id=result.request.id,
        reference=result.request.reference,
        created_at=result.request.created_at,
    )


@admin_router.get("/requests", response_model=PqrsfPageResponse, summary="List PQRSF requests")
@inject
async def list_requests(
    request_type_id: UUID | None = None,
    site_id: UUID | None = None,
    reference: str | None = Query(default=None, max_length=50),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    _: UserModel = Depends(require_admin),  # noqa: B008
    service: PqrsfService = _service,
) -> PqrsfPageResponse:
    """List requests for administrators."""

    result = await service.list_requests(
        PqrsfFilters(request_type_id=request_type_id, site_id=site_id, reference=reference),
        page,
        size,
    )
    return _page(result)


@admin_router.get(
    "/requests/{request_id}", response_model=PqrsfRequestResponse, summary="Get PQRSF request"
)
@inject
async def get_request(
    request_id: UUID,
    _: UserModel = Depends(require_admin),  # noqa: B008
    service: PqrsfService = _service,
) -> PqrsfRequestResponse:
    """Return a request to an administrator."""

    return _request(await service.get_request(request_id))


@admin_router.get(
    "/requests/{request_id}/attachments/{attachment_id}/download",
    summary="Download PQRSF attachment",
    response_class=Response,
)
@inject
async def download_attachment(
    request_id: UUID,
    attachment_id: UUID,
    _: UserModel = Depends(require_admin),  # noqa: B008
    service: PqrsfService = _service,
) -> Response:
    """Return a private attachment only to an administrator."""

    attachment, content = await service.download_attachment(request_id, attachment_id)
    filename = quote(attachment.original_filename)
    return Response(
        content=content,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _page(value: PqrsfPage) -> PqrsfPageResponse:
    return PqrsfPageResponse(
        items=[_request(item) for item in value.items],
        total=value.total,
        page=value.page,
        size=value.size,
    )


__all__ = ["admin_router", "public_router"]
