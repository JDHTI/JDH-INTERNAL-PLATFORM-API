"""Manager screens for suggestions and compliments (kept apart from the cases)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel

from app.app_container import ApplicationContainer
from app.core.rate_limit import rate_limit
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.pqrsf.application.feedback import FeedbackService
from app.modules.pqrsf.domain.feedback import Feedback, FeedbackFile, FeedbackFilters

router = APIRouter(prefix="/pqrsf/feedback", dependencies=[Depends(rate_limit("relaxed"))])
_service = Depends(Provide[ApplicationContainer.v1_container.pqrsf_feedback_service])
_principal = Depends(get_current_principal)


class FeedbackKindResponse(BaseModel):
    """Suggestion or compliment."""

    id: UUID
    code: str
    name: str


class FeedbackSiteResponse(BaseModel):
    """Site the message was sent for, when the sender said."""

    id: UUID
    code: str
    name: str


class FeedbackReplyResponse(BaseModel):
    """Where the automatic thank-you stands."""

    status: Literal["pending", "sent", "failed", "skipped"]
    sent_at: datetime | None
    # Why there is no reply: ``recipient_limit``, ``same_conversation`` or ``disabled``.
    skipped_reason: str | None


class FeedbackItemResponse(BaseModel):
    """A message in the list."""

    id: UUID
    kind: FeedbackKindResponse
    origin: Literal["public_form", "email"]
    full_name: str
    business_name: str | None
    email: str
    subject: str | None
    body_excerpt: str
    site: FeedbackSiteResponse | None
    files_count: int
    reply: FeedbackReplyResponse
    read_at: datetime | None
    created_at: datetime


class FeedbackFileResponse(BaseModel):
    """A file that came with a message."""

    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime


class PersonResponse(BaseModel):
    """Who opened the message first."""

    id: UUID
    display_name: str | None
    email: str | None


class FeedbackDetailResponse(FeedbackItemResponse):
    """A message in full, with its files."""

    body: str
    files: list[FeedbackFileResponse]
    read_by: PersonResponse | None


class FeedbackPageResponse(BaseModel):
    """One page of messages."""

    items: list[FeedbackItemResponse]
    total: int
    page: int
    size: int


class FeedbackKindCountResponse(BaseModel):
    """Messages of one type."""

    code: str
    name: str
    count: int
    unread: int


class FeedbackSummaryResponse(BaseModel):
    """Totals for the menu badge and the filters."""

    total: int
    unread: int
    by_kind: list[FeedbackKindCountResponse]


def _item_fields(value: Feedback) -> dict:
    kind = value.request_type
    return {
        "id": value.id,
        "kind": FeedbackKindResponse(id=kind.id, code=kind.code, name=kind.name),
        "origin": value.origin,
        "full_name": value.full_name,
        "business_name": value.business_name,
        "email": value.email,
        "subject": value.subject,
        "body_excerpt": value.excerpt,
        "site": (
            FeedbackSiteResponse(id=value.site.id, code=value.site.code, name=value.site.name)
            if value.site
            else None
        ),
        "files_count": value.files_count,
        "reply": FeedbackReplyResponse(
            status=value.reply_status or "pending",
            sent_at=value.reply_sent_at,
            skipped_reason=value.reply_skipped_reason,
        ),
        "read_at": value.read_at,
        "created_at": value.created_at,
    }


def _file(value: FeedbackFile) -> FeedbackFileResponse:
    return FeedbackFileResponse(
        id=value.id,
        filename=value.original_filename,
        content_type=value.content_type,
        size_bytes=value.size_bytes,
        created_at=value.created_at,
    )


@router.get("/summary", response_model=FeedbackSummaryResponse, summary="Feedback totals")
@inject
async def summary(
    principal: Principal = _principal, service: FeedbackService = _service
) -> FeedbackSummaryResponse:
    """Totals and unread messages per type, for the menu badge (PQRSF managers)."""

    value = await service.summary(principal)
    return FeedbackSummaryResponse(
        total=value.total,
        unread=value.unread,
        by_kind=[FeedbackKindCountResponse(**vars(kind)) for kind in value.by_kind],
    )


@router.get("", response_model=FeedbackPageResponse, summary="List suggestions and compliments")
@inject
async def list_feedback(
    kind: Literal["sugerencia", "felicitacion"] | None = None,
    q: str | None = Query(default=None, max_length=100),  # noqa: B008
    unread: bool = False,
    date_from: date | None = Query(default=None, alias="from"),  # noqa: B008
    date_to: date | None = Query(default=None, alias="to"),  # noqa: B008
    page: int = Query(default=1, ge=1),  # noqa: B008
    size: int = Query(default=20, ge=1, le=100),  # noqa: B008
    principal: Principal = _principal,
    service: FeedbackService = _service,
) -> FeedbackPageResponse:
    """Newest first; only PQRSF managers can read them."""

    result = await service.list_feedback(
        principal,
        FeedbackFilters(
            request_type_code=kind,
            query=q,
            unread_only=unread,
            created_from=date_from,
            created_to=date_to,
        ),
        page,
        size,
    )
    return FeedbackPageResponse(
        items=[FeedbackItemResponse(**_item_fields(item)) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.get("/{feedback_id}", response_model=FeedbackDetailResponse, summary="Read a message")
@inject
async def get_feedback(
    feedback_id: UUID, principal: Principal = _principal, service: FeedbackService = _service
) -> FeedbackDetailResponse:
    """The message in full; the first opening marks it as read."""

    value = await service.open(feedback_id, principal)
    reader = await service.reader(value)
    return FeedbackDetailResponse(
        **_item_fields(value),
        body=value.body,
        files=[_file(item) for item in value.files],
        read_by=(
            PersonResponse(id=reader.id, display_name=reader.display_name, email=reader.email)
            if reader
            else None
        ),
    )


@router.get(
    "/{feedback_id}/files/{file_id}/download",
    summary="Download a file of a message",
    response_class=Response,
)
@inject
async def download_file(
    feedback_id: UUID,
    file_id: UUID,
    principal: Principal = _principal,
    service: FeedbackService = _service,
) -> Response:
    """Return one stored file; it is always sent as a download, never rendered."""

    found, content = await service.download(feedback_id, file_id, principal)
    return Response(
        content=content,
        media_type=found.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(found.original_filename)}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = ["router"]
