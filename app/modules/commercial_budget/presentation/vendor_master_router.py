"""Endpoints of the rep master upload (``/vendor-master``)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from pydantic import BaseModel

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.commercial_budget.application.vendor_master import VendorMasterService
from app.modules.commercial_budget.domain.modules import VENDOR_MASTER
from app.modules.commercial_budget.infrastructure.vendor_template import FILENAME
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal

router = APIRouter()
_GATE = Depends(require_permission(VENDOR_MASTER))
_service = Depends(Provide[ApplicationContainer.v1_container.vendor_master_service])
_principal = Depends(get_current_principal)
_FILE = File(...)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class StatusResponse(BaseModel):
    # Whether the API has dwh-platform's URL (the upload works).
    configured: bool
    can_read: bool
    can_manage: bool


class UploadRecordResponse(BaseModel):
    id: UUID
    filename: str
    size: int
    status: Literal["cargado", "rechazado", "error"]
    http_status: int | None
    rejected_rows: int
    warnings: int
    summary: dict[str, Any]
    actor: str
    at: datetime


@router.get("/status", response_model=StatusResponse, summary="Whether the upload works")
@inject
async def status(
    principal: Principal = _principal, service: VendorMasterService = _service
) -> StatusResponse:
    """Open to everyone: ``false`` when the caller has no access (no 403)."""

    result = await service.status(principal)
    return StatusResponse(
        configured=result.configured, can_read=result.can_read, can_manage=result.can_manage
    )


@router.get(
    "/template",
    dependencies=[_GATE],
    summary="The empty «Maestro Vendedores JDH» template",
    response_class=Response,
)
@inject
async def template(
    principal: Principal = _principal, service: VendorMasterService = _service
) -> Response:
    content = await service.template(principal)
    return Response(
        content=content,
        media_type=XLSX,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(FILENAME)}"},
    )


@router.post("/upload", dependencies=[_GATE], summary="Upload the rep master to the DWH")
@inject
async def upload(
    file: UploadFile = _FILE,
    principal: Principal = _principal,
    service: VendorMasterService = _service,
) -> dict[str, Any]:
    """Forwarded to dwh-platform, which writes it in the warehouse (reps missing from the file
    do not change). Its answer comes back as it is: ``vendedores_procesados`` and the other
    counts, ``filas_rechazadas`` (sheet, row, identifier, reasons), ``advertencias`` and
    ``transformacion`` (whether dbt was scheduled to publish it). 400 when it rejects the
    file; 503 when it is not configured or cannot be reached."""

    content = await file.read(service.max_upload_bytes + 1)
    return await service.upload(principal, file.filename, content)


@router.get(
    "/uploads",
    response_model=list[UploadRecordResponse],
    dependencies=[_GATE],
    summary="The last uploads and what dwh-platform answered",
)
@inject
async def uploads(
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = _principal,
    service: VendorMasterService = _service,
) -> list[UploadRecordResponse]:
    found = await service.uploads(principal, limit)
    return [UploadRecordResponse(**vars(item)) for item in found]


__all__ = ["router"]
