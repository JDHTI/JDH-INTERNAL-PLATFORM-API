"""Administrator endpoints for the shared master catalog."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.app_container import ApplicationContainer
from app.core.rate_limit import rate_limit
from app.modules.identity.infrastructure.models import UserModel
from app.modules.identity.presentation.router import require_admin
from app.modules.masters.application.service import MAX_PAGE_SIZE, MasterService
from app.modules.masters.domain.entities import MasterField, MasterRecord, MasterSummary

router = APIRouter(dependencies=[Depends(rate_limit("medium"))])
_service = Depends(Provide[ApplicationContainer.v1_container.master_service])


class MasterSummaryResponse(BaseModel):
    """Master metadata and record count."""

    slug: str
    name: str
    description: str | None
    source_system: str
    source_object: str | None
    grain: str | None
    natural_key: list[str]
    record_count: int
    last_synced_at: datetime | None


class MasterFieldResponse(BaseModel):
    """Declared column of a master."""

    name: str
    label: str
    data_type: str
    role: str
    is_nullable: bool
    is_pii: bool
    references: str | None


class MasterDetailResponse(MasterSummaryResponse):
    """Master metadata plus its declared fields."""

    fields: list[MasterFieldResponse]


class MasterRecordResponse(BaseModel):
    """Normalized master record."""

    id: UUID
    company_id: int | None
    code: str | None
    name: str | None
    is_active: bool | None
    source_key: str
    attributes: dict[str, Any]
    synced_at: datetime | None


class MasterRecordPageResponse(BaseModel):
    """Page of master records."""

    items: list[MasterRecordResponse]
    total: int
    offset: int
    limit: int


def _summary(summary: MasterSummary) -> dict[str, Any]:
    return MasterSummaryResponse.model_validate(summary, from_attributes=True).model_dump()


def _field(field: MasterField) -> MasterFieldResponse:
    return MasterFieldResponse.model_validate(field, from_attributes=True)


def _record(record: MasterRecord) -> MasterRecordResponse:
    return MasterRecordResponse.model_validate(record, from_attributes=True)


@router.get("", response_model=list[MasterSummaryResponse], summary="List masters")
@inject
async def list_masters(
    _: UserModel = Depends(require_admin),  # noqa: B008
    service: MasterService = _service,
) -> list[MasterSummaryResponse]:
    """List active masters with their record counts."""

    return [
        MasterSummaryResponse.model_validate(summary, from_attributes=True)
        for summary in await service.list_masters()
    ]


@router.get("/{slug}", response_model=MasterDetailResponse, summary="Get master")
@inject
async def get_master(
    slug: str,
    _: UserModel = Depends(require_admin),  # noqa: B008
    service: MasterService = _service,
) -> MasterDetailResponse:
    """Return one master with its declared fields."""

    detail = await service.get_master(slug)
    return MasterDetailResponse(
        **_summary(detail.summary), fields=[_field(field) for field in detail.fields]
    )


@router.get(
    "/{slug}/records",
    response_model=MasterRecordPageResponse,
    summary="List master records",
)
@inject
async def list_master_records(
    slug: str,
    q: str | None = Query(default=None, max_length=100),
    is_active: bool | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    _: UserModel = Depends(require_admin),  # noqa: B008
    service: MasterService = _service,
) -> MasterRecordPageResponse:
    """Search a master's records by code or name."""

    page = await service.list_records(slug, q, is_active, offset, limit)
    return MasterRecordPageResponse(
        items=[_record(record) for record in page.items],
        total=page.total,
        offset=page.offset,
        limit=page.limit,
    )


__all__ = ["router"]
