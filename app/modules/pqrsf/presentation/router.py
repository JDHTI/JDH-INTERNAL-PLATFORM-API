"""Public PQRSF form and protected administrator endpoints."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile, status
from pydantic import BaseModel, EmailStr, Field, TypeAdapter, ValidationError

from app.app_container import ApplicationContainer
from app.core.errors import InvalidInputError
from app.modules.identity.infrastructure.models import UserModel
from app.modules.identity.presentation.router import require_admin
from app.modules.pqrsf.application.service import PqrsfService
from app.modules.pqrsf.domain.entities import (
    Cause,
    ImageUpload,
    PqrsfFilters,
    PqrsfPage,
    PqrsfRequest,
    ProductInput,
    RequestType,
    Subject,
)
from app.modules.sites.domain.entities import Site

public_router = APIRouter(prefix="/public/pqrsf")
admin_router = APIRouter(prefix="/pqrsf")
_service = Depends(Provide[ApplicationContainer.v1_container.pqrsf_service])


class RequestTypeResponse(BaseModel):
    """Public type catalog item."""

    id: UUID
    code: str
    name: str
    allows_document_request: bool
    allows_attachments: bool
    # ``acknowledge``: a suggestion or compliment, answered with thanks and no case.
    handling: Literal["case", "acknowledge"] = "case"
    # One short line to show under the option, always visible (at most 120 characters).
    description: str | None = None


class CauseResponse(BaseModel):
    """A cause the requester can pick once a subject is chosen."""

    id: UUID
    code: str
    name: str
    description: str | None
    # "Otro": the description becomes required.
    is_other: bool
    # Whether the form shows the product lines, and whether at least one is required.
    allows_product: bool
    requires_product: bool


class SubjectResponse(BaseModel):
    """A subject of the public form, with the causes to pick from (may be none)."""

    id: UUID
    code: str
    name: str
    description: str | None
    # Codes of the request types that offer it.
    request_type_codes: list[str]
    # Area it is filed under on the form (a filter only: the cause decides the case's area).
    dependency_id: UUID | None = None
    is_other: bool
    causes: list[CauseResponse]


class PublicDependencyResponse(BaseModel):
    """An area the form lets the requester narrow the subjects by."""

    id: UUID
    code: str
    name: str


class ProductOptionResponse(BaseModel):
    """A product found by reference or name; its supplier is never exposed publicly."""

    sku: str
    description: str | None


class ProductLineRequest(BaseModel):
    """A product line of a complaint or claim, sent as JSON inside the form."""

    invoice_number: str = Field(min_length=1, max_length=40)
    sku: str = Field(min_length=1, max_length=40)
    lot: str = Field(min_length=1, max_length=60)
    expiry_date: date
    affected_quantity: int = Field(ge=1, le=1_000_000)


_PRODUCT_LINES = TypeAdapter(list[ProductLineRequest])


class ClassificationRefResponse(BaseModel):
    """Subject or cause picked by the requester."""

    id: UUID
    code: str
    name: str


class ClaimedProductResponse(BaseModel):
    """A product line as the requester sent it, with the master data seen then."""

    invoice_number: str
    sku: str
    description: str | None
    supplier_code: str | None
    supplier_name: str | None
    lot: str
    expiry_date: date
    affected_quantity: int


class PqrsfSiteResponse(BaseModel):
    """Public site catalog item."""

    id: UUID
    code: str
    name: str


class DocumentOptionResponse(BaseModel):
    """A document the customer can ask for."""

    id: UUID
    name: str
    # A certificate answers it at once by email: no case and no waiting.
    automatic: bool = False


class DocumentCategoryResponse(BaseModel):
    """A group of documents offered together."""

    id: UUID
    code: str
    name: str
    documents: list[DocumentOptionResponse]


class PqrsfCatalogResponse(BaseModel):
    """Catalog payload used by the isolated public form."""

    request_types: list[RequestTypeResponse]
    sites: list[PqrsfSiteResponse]
    document_categories: list[DocumentCategoryResponse]
    # Subjects with their causes; each lists the request types that offer it.
    subjects: list[SubjectResponse] = []
    # Active areas some active subject belongs to, for the area filter of the form.
    dependencies: list[PublicDependencyResponse] = []
    limits: AttachmentLimitsResponse | None = None


class AttachmentLimitsResponse(BaseModel):
    """Evidence the public form accepts, so the interface can warn before sending."""

    max_images: int
    max_image_mb: int
    max_total_image_mb: int
    image_extensions: list[str]
    max_videos: int
    max_video_mb: int
    video_extensions: list[str]


class PqrsfReceiptResponse(BaseModel):
    """Minimal public acknowledgement."""

    id: UUID
    reference: str
    # Consecutive the customer can quote (P26AC0012); null if the case
    # could not be opened at submission time.
    tracking_code: str | None = None
    # ``feedback`` for a suggestion or compliment: no case and no consecutive, only thanks.
    handled_as: Literal["case", "feedback", "documents"] = "case"
    # With `documents` (and on a mixed request) the certificates already sent to the email.
    delivered_documents: list[str] = []
    created_at: datetime


class OpenCaseResponse(BaseModel):
    """Consecutive of the case opened for a request."""

    tracking_code: str


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
    subject: str | None
    description: str | None
    document_request: bool
    selected_subject: ClassificationRefResponse | None = None
    selected_cause: ClassificationRefResponse | None = None
    products: list[ClaimedProductResponse] = []
    pickup_order_requested: Literal["yes", "no", "unknown"] | None = None
    pickup_order_number: str | None = None
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


def _ref(value: Subject | Cause | None) -> ClassificationRefResponse | None:
    if value is None:
        return None
    return ClassificationRefResponse(id=value.id, code=value.code, name=value.name)


def _subject(value: Subject) -> SubjectResponse:
    return SubjectResponse(
        id=value.id,
        code=value.code,
        name=value.name,
        description=value.description,
        request_type_codes=list(value.request_type_codes),
        dependency_id=value.dependency_id,
        is_other=value.is_other,
        causes=[
            CauseResponse.model_validate(cause, from_attributes=True) for cause in value.causes
        ],
    )


def _product_lines(value: str | None) -> list[ProductInput]:
    """Parse the JSON array of product lines sent in the multipart form."""

    if value is None or not value.strip():
        return []
    try:
        lines = _PRODUCT_LINES.validate_json(value)
    except ValidationError as exc:
        raise InvalidInputError(
            "Los productos deben ser una lista con número de factura, referencia, lote, "
            "fecha de vencimiento (AAAA-MM-DD) y cantidad afectada mayor que cero"
        ) from exc
    return [ProductInput(**line.model_dump()) for line in lines]


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
        subject=value.subject,
        description=value.description,
        document_request=value.document_request,
        selected_subject=_ref(value.subject_ref),
        selected_cause=_ref(value.cause_ref),
        products=[
            ClaimedProductResponse.model_validate(product, from_attributes=True)
            for product in value.products
        ],
        pickup_order_requested=value.pickup_order_requested,
        pickup_order_number=value.pickup_order_number,
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
)
@inject
async def public_catalog(service: PqrsfService = _service) -> PqrsfCatalogResponse:
    """Return active types, sites, document categories and subjects for the public form."""

    request_types, sites, categories = await service.catalog()
    return PqrsfCatalogResponse(
        request_types=[_type(value) for value in request_types],
        sites=[_site(value) for value in sites],
        document_categories=[
            DocumentCategoryResponse.model_validate(value, from_attributes=True)
            for value in categories
        ],
        subjects=[_subject(value) for value in await service.subjects()],
        dependencies=[
            PublicDependencyResponse(id=value.id, code=value.code, name=value.name)
            for value in await service.subject_dependencies()
        ],
        limits=AttachmentLimitsResponse(
            max_images=service.max_attachments,
            max_image_mb=service.max_attachment_bytes // (1024 * 1024),
            max_total_image_mb=service.max_total_attachment_bytes // (1024 * 1024),
            image_extensions=["jpg", "jpeg", "png", "webp"],
            max_videos=service.max_videos,
            max_video_mb=service.max_video_bytes // (1024 * 1024),
            video_extensions=["mp4", "mov", "webm"],
        ),
    )


@public_router.get(
    "/products",
    response_model=list[ProductOptionResponse],
    summary="Search products for the public form",
)
@inject
async def search_public_products(
    q: str = Query(min_length=2, max_length=60),
    limit: int = Query(default=10, ge=1, le=20),
    service: PqrsfService = _service,
) -> list[ProductOptionResponse]:
    """Find active products by JDH reference (exact) or by part of their name."""

    return [
        ProductOptionResponse(sku=value.sku, description=value.description)
        for value in await service.search_products(q, limit)
    ]


@public_router.post(
    "/requests",
    response_model=PqrsfReceiptResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a public PQRSF request",
    # Limits (per address, global, size, CAPTCHA) are enforced before the body is read by
    # PublicIntakeGuard, see app/core/public_guard.py.
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
    subject: str | None = Form(default=None),  # noqa: B008
    description: str | None = Form(default=None),  # noqa: B008
    document_request: bool = Form(default=False),  # noqa: B008
    document_category_id: UUID | None = Form(default=None),  # noqa: B008
    document_type_ids: list[UUID] = Form(default=[]),  # noqa: B008
    document_other: str | None = Form(default=None),  # noqa: B008
    subject_id: UUID | None = Form(default=None),  # noqa: B008
    cause_id: UUID | None = Form(default=None),  # noqa: B008
    products: str | None = Form(default=None),  # noqa: B008
    pickup_order_requested: Literal["yes", "no", "unknown"] | None = Form(default=None),  # noqa: B008
    pickup_order_number: str | None = Form(default=None, max_length=40),  # noqa: B008
    images: list[UploadFile] = File(default=[]),  # noqa: B008
    videos: list[UploadFile] = File(default=[]),  # noqa: B008
    idempotency_key: str = Header(alias="Idempotency-Key"),
    service: PqrsfService = _service,
) -> PqrsfReceiptResponse:
    """Create a request without requiring an Entra session.

    The endpoint accepts multipart form fields and optional images and videos. ``products`` is a
    JSON array of product lines (see ``ProductLineRequest``).
    """

    product_lines = _product_lines(products)
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
        "subject": subject,
        "description": description,
        "document_request": document_request,
        "document_category_id": str(document_category_id) if document_category_id else None,
        "document_type_ids": sorted(str(value) for value in document_type_ids),
        "document_other": document_other,
        "subject_id": str(subject_id) if subject_id else None,
        "cause_id": str(cause_id) if cause_id else None,
        "products": [line.__dict__ for line in product_lines],
        "pickup_order_requested": pickup_order_requested,
        "pickup_order_number": pickup_order_number,
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
        subject=subject,
        description=description,
        document_request=document_request,
        document_category_id=document_category_id,
        document_type_ids=document_type_ids,
        document_other=document_other,
        subject_id=subject_id,
        cause_id=cause_id,
        products=product_lines,
        pickup_order_requested=pickup_order_requested,
        pickup_order_number=pickup_order_number,
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
        videos=[
            ImageUpload(
                filename=video.filename,
                content_type=video.content_type,
                content=await video.read(service.max_video_bytes + 1),
            )
            for video in videos
        ],
    )
    return PqrsfReceiptResponse(
        id=result.request.id,
        reference=result.request.reference,
        tracking_code=result.tracking_code,
        handled_as=result.handled_as if result.handled_as in ("feedback", "documents") else "case",
        delivered_documents=result.delivered,
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


@admin_router.post(
    "/requests/{request_id}/case",
    response_model=OpenCaseResponse,
    summary="Open the follow-up case of a request",
)
@inject
async def open_request_case(
    request_id: UUID,
    _: UserModel = Depends(require_admin),  # noqa: B008
    service: PqrsfService = _service,
) -> OpenCaseResponse:
    """Open the case of a request whose case failed at submission; idempotent."""

    return OpenCaseResponse(tracking_code=await service.open_case(request_id))


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
