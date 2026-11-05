"""Master catalog endpoints (``/masters``).

Administrators and moderators read every master. Anyone else reads the masters
switched on in the areas where their role grants ``maestros``; ``?area=`` narrows the
list to one area's masters. Personal data fields go only to whoever manages the module.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.domain.principal import Principal
from app.modules.masters.application.service import MAX_PAGE_SIZE, MasterService
from app.modules.masters.domain.entities import MasterField, MasterRecord, MasterSummary
from app.modules.masters.infrastructure.export import master_workbook

router = APIRouter()
_service = Depends(Provide[ApplicationContainer.v1_container.master_service])
_reader = Depends(require_permission("maestros"))

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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
    area: str | None = Query(default=None, max_length=50, description="Only this area's masters"),
    principal: Principal = _reader,
    service: MasterService = _service,
) -> list[MasterSummaryResponse]:
    """Maestros que puedes consultar, con su número de registros y última sincronización."""

    return [
        MasterSummaryResponse.model_validate(summary, from_attributes=True)
        for summary in await service.list_masters(principal, area)
    ]


@router.get("/{slug}", response_model=MasterDetailResponse, summary="Get master")
@inject
async def get_master(
    slug: str,
    principal: Principal = _reader,
    service: MasterService = _service,
) -> MasterDetailResponse:
    """Un maestro con sus campos declarados."""

    detail = await service.get_master(slug, principal)
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
    principal: Principal = _reader,
    service: MasterService = _service,
) -> MasterRecordPageResponse:
    """Busca registros de un maestro por código o nombre."""

    page = await service.list_records(slug, q, is_active, offset, limit, principal)
    return MasterRecordPageResponse(
        items=[_record(record) for record in page.items],
        total=page.total,
        offset=page.offset,
        limit=page.limit,
    )


@router.get(
    "/{slug}/records.xlsx",
    summary="Download a master",
    response_class=Response,
    responses={200: {"content": {_XLSX: {}}}},
)
@inject
async def download_master(
    slug: str,
    is_active: bool | None = None,
    principal: Principal = _reader,
    service: MasterService = _service,
) -> Response:
    """Descarga el maestro en Excel, con los campos que puedes ver."""

    detail, records = await service.export(slug, is_active, principal)
    # ASCII fallback plus the master's display name (RFC 5987) for the browser.
    name = quote(f"{detail.summary.name}.xlsx", safe="")
    disposition = f"attachment; filename=\"{detail.summary.slug}.xlsx\"; filename*=UTF-8''{name}"
    return Response(
        content=master_workbook(detail, records),
        media_type=_XLSX,
        headers={"Content-Disposition": disposition},
    )


__all__ = ["router"]
