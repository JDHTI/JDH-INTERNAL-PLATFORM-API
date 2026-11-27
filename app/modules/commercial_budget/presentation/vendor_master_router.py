"""Endpoints of the rep master upload (``/vendor-master``)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, File, Path, Query, Response, UploadFile
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.commercial_budget.application.vendor_master import VendorMasterService
from app.modules.commercial_budget.domain.modules import VENDOR_MASTER
from app.modules.commercial_budget.domain.vendor_master import FIELD_LABELS
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
    # Whether a new team or a class change is written in the DWH at once (externo.equipo,
    # 2026-11-27); false: it waits in the platform until the next upload.
    writes_teams: bool = False


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
        configured=result.configured,
        can_read=result.can_read,
        can_manage=result.can_manage,
        writes_teams=result.writes_teams,
    )


@router.get(
    "/template",
    dependencies=[_GATE],
    summary="The empty «Maestro Vendedores JDH» template",
    response_class=Response,
)
@inject
async def template(
    empty: bool = Query(default=False, description="Without the current reps"),
    principal: Principal = _principal,
    service: VendorMasterService = _service,
) -> Response:
    """Filled with the reps, coordinators and directors the warehouse has today (unless
    ``empty``); EQUIPO only takes a team of the master and CLASE follows it."""

    content = await service.template(principal, empty)
    return Response(
        content=content,
        media_type=XLSX,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(FILENAME)}"},
    )


class MasterTeamResponse(BaseModel):
    """A team of the master: ``dwh`` (published by the warehouse) or ``plataforma`` (created
    here, not loaded yet). An inactive one is not offered by the template."""

    name: str
    team_class: str | None
    source: Literal["dwh", "plataforma"]
    is_active: bool
    # Active reps in the team in the warehouse today.
    reps: int
    note: str | None


class TeamCreate(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    team_class: str = Field(min_length=2, max_length=40)
    note: str | None = Field(default=None, max_length=500)


class TeamUpdate(BaseModel):
    team_class: str | None = Field(default=None, min_length=2, max_length=40)
    is_active: bool | None = None
    note: str | None = Field(default=None, max_length=500)


def _team(item) -> MasterTeamResponse:
    return MasterTeamResponse(
        name=item.name,
        team_class=item.team_class,
        source=item.source,
        is_active=item.is_active,
        reps=item.reps,
        note=item.note,
    )


@router.get(
    "/teams",
    response_model=list[MasterTeamResponse],
    dependencies=[_GATE],
    summary="The team master the template offers",
)
@inject
async def list_teams(
    principal: Principal = _principal, service: VendorMasterService = _service
) -> list[MasterTeamResponse]:
    return [_team(item) for item in await service.teams(principal)]


@router.post(
    "/teams",
    response_model=MasterTeamResponse,
    status_code=201,
    dependencies=[_GATE],
    summary="Create a team (offered by the template until an upload loads it)",
)
@inject
async def create_team(
    body: TeamCreate, principal: Principal = _principal, service: VendorMasterService = _service
) -> MasterTeamResponse:
    """``manage``. 409 when the master already has it."""

    return _team(await service.create_team(principal, body.name, body.team_class, body.note))


@router.patch(
    "/teams/{name}",
    response_model=MasterTeamResponse,
    dependencies=[_GATE],
    summary="Change a team's class or hide it from the template",
)
@inject
async def update_team(
    body: TeamUpdate,
    name: str = Path(min_length=2, max_length=60),
    principal: Principal = _principal,
    service: VendorMasterService = _service,
) -> MasterTeamResponse:
    """``manage``. A new class reaches the warehouse with the next upload."""

    return _team(
        await service.update_team(
            principal,
            name,
            team_class=body.team_class,
            is_active=body.is_active,
            note=body.note,
        )
    )


class ChangeResponse(BaseModel):
    salesperson_code: str
    name: str | None
    field: Literal["team", "team_class", "regional", "business_unit"]
    field_label: str
    current: str | None
    new: str | None


class ProblemResponse(BaseModel):
    sheet: str
    row: int
    value: str | None
    reason: str


class TeamResponse(BaseModel):
    team: str
    # Active reps in the team today and after the upload (reps not in the file keep theirs).
    current: int
    new: int


class MissingResponse(BaseModel):
    salesperson_code: str
    name: str | None
    team: str | None
    # Sale lines this month and the previous one.
    lines: int


class CoordinatorResponse(BaseModel):
    document: str | None
    name: str | None
    team: str | None
    reason: str


class PreviewResponse(BaseModel):
    """What the upload would change; nothing is written."""

    reps_read: int
    reps_resolved: int
    changed_reps: int
    unchanged: int
    changes: list[ChangeResponse]
    rejected: list[ProblemResponse]
    warnings: list[ProblemResponse]
    new_teams: list[str]
    emptied_teams: list[str]
    teams: list[TeamResponse]
    missing: list[MissingResponse]
    coordinators: list[CoordinatorResponse]


@router.post(
    "/preview",
    response_model=PreviewResponse,
    dependencies=[_GATE],
    summary="What uploading the file would change (nothing is written)",
)
@inject
async def preview(
    file: UploadFile = _FILE,
    principal: Principal = _principal,
    service: VendorMasterService = _service,
) -> PreviewResponse:
    """Reads the workbook with dwh-platform's rules and compares it with ``consumo.vendedores``
    today. ``changes`` has each rep's team, class, regional and business unit that would
    change. Also reported: the rows the loader would reject, teams created or left with no
    rep, active reps with sales this month or last that the file leaves out (they keep their
    assignment), and coordinators whose team is no rep's team in the file (the link to
    their reps breaks)."""

    content = await file.read(service.max_upload_bytes + 1)
    result = await service.preview(principal, file.filename, content)
    return PreviewResponse(
        reps_read=result.reps_read,
        reps_resolved=result.reps_resolved,
        changed_reps=result.changed_reps,
        unchanged=result.unchanged,
        changes=[
            ChangeResponse(
                salesperson_code=item.code,
                name=item.name,
                field=item.field,  # type: ignore[arg-type]
                field_label=FIELD_LABELS[item.field],
                current=item.current,
                new=item.new,
            )
            for item in result.changes
        ],
        rejected=[ProblemResponse(**vars(item)) for item in result.rejected],
        warnings=[ProblemResponse(**vars(item)) for item in result.warnings],
        new_teams=result.new_teams,
        emptied_teams=result.emptied_teams,
        teams=[TeamResponse(**vars(item)) for item in result.teams],
        missing=[
            MissingResponse(
                salesperson_code=item.code, name=item.name, team=item.team, lines=item.lines
            )
            for item in result.missing
        ],
        coordinators=[CoordinatorResponse(**vars(item)) for item in result.coordinators],
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
