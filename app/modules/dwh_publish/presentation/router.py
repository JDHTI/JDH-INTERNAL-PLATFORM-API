"""Publication to the DWH (``/dwh-publish``): its state, failed events, retry, reconcile.

Administrators only.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.modules.dwh_publish.application.service import DwhPublishService
from app.modules.identity.infrastructure.models import UserModel
from app.modules.identity.presentation.router import require_admin

router = APIRouter()
_service = Depends(Provide[ApplicationContainer.v1_container.dwh_publish_service])
_admin = Depends(require_admin)


class TopicStatusResponse(BaseModel):
    topic: str
    pending: int
    failed: int
    published: int
    oldest_pending_at: datetime | None
    last_published_at: datetime | None
    last_error: str | None


class ReconcileResponse(BaseModel):
    topic: str
    local: int
    remote: int
    queued: int


class PublishStatusResponse(BaseModel):
    enabled: bool
    configured: bool
    last_run_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    paused_until: datetime | None
    last_reconcile_at: datetime | None
    last_reconcile: list[ReconcileResponse]
    topics: list[TopicStatusResponse]


class FailedEventResponse(BaseModel):
    id: int
    topic: str
    entity_id: UUID
    version: int
    attempts: int
    last_error: str | None
    created_at: datetime


class RetryRequest(BaseModel):
    # Empty or missing: every failed event.
    ids: list[int] | None = Field(default=None, max_length=1000)


class RetryResponse(BaseModel):
    requeued: int


@router.get("/dwh-publish", response_model=PublishStatusResponse)
@inject
async def publish_status(
    _: UserModel = _admin, service: DwhPublishService = _service
) -> PublishStatusResponse:
    """What is pending, failed and published, and the worker's last pass."""

    overview = await service.overview()
    state = overview.state
    return PublishStatusResponse(
        enabled=overview.enabled,
        configured=overview.configured,
        last_run_at=state.last_run_at,
        last_success_at=state.last_success_at,
        last_error=state.last_error,
        paused_until=state.paused_until,
        last_reconcile_at=state.last_reconcile_at,
        last_reconcile=[ReconcileResponse(**item.__dict__) for item in state.last_reconcile],
        topics=[TopicStatusResponse(**item.__dict__) for item in overview.topics],
    )


@router.get("/dwh-publish/failed", response_model=list[FailedEventResponse])
@inject
async def failed_events(
    limit: int = Query(100, ge=1, le=500),
    _: UserModel = _admin,
    service: DwhPublishService = _service,
) -> list[FailedEventResponse]:
    """Events that ran out of attempts, newest first."""

    return [FailedEventResponse(**item.__dict__) for item in await service.failed(limit)]


@router.post("/dwh-publish/retry", response_model=RetryResponse)
@inject
async def retry_failed(
    body: RetryRequest,
    _: UserModel = _admin,
    service: DwhPublishService = _service,
) -> RetryResponse:
    """Send failed events again from zero attempts."""

    return RetryResponse(requeued=await service.retry(body.ids))


@router.post("/dwh-publish/reconcile", response_model=list[ReconcileResponse])
@inject
async def reconcile(
    _: UserModel = _admin, service: DwhPublishService = _service
) -> list[ReconcileResponse]:
    """Compare the warehouse with the platform and queue what it lacks."""

    return [ReconcileResponse(**item.__dict__) for item in await service.reconcile_now()]
