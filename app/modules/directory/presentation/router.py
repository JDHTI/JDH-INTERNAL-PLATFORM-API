"""Directory endpoints for everyone signed in (``/directory``)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Header, Query, Response, status

from app.app_container import ApplicationContainer
from app.modules.directory.application.service import MAX_PAGE_SIZE, DirectoryService
from app.modules.directory.presentation.schemas import (
    BirthdaysResponse,
    EmployeeDetail,
    EmployeeSummary,
    ItemsResponse,
    NewHireSummary,
    PageResponse,
    SiteResponse,
    detail,
    site,
    summary,
)
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal

router = APIRouter()
_service = Depends(Provide[ApplicationContainer.v1_container.directory_service])
_principal = Depends(get_current_principal)

# A versioned photo URL (``?v=``) never changes: the browser keeps it for a year.
_IMMUTABLE = "private, max-age=31536000, immutable"
_REVALIDATE = "private, max-age=3600"


@router.get("/employees", response_model=PageResponse[EmployeeSummary])
@inject
async def list_employees(
    q: str | None = Query(None, max_length=200),
    site_id: UUID | None = None,
    department: str | None = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    size: int = Query(24, ge=1, le=MAX_PAGE_SIZE),
    _: Principal = _principal,
    service: DirectoryService = _service,
) -> dict[str, Any]:
    """Directorio público por nombre; ``q`` busca sin tildes por inicio de palabra."""

    items, total = await service.list_employees(q, site_id, department, page, size)
    return {"items": [summary(item) for item in items], "total": total, "page": page, "size": size}


@router.get("/employees/me", response_model=EmployeeDetail)
@inject
async def my_employee(
    principal: Principal = _principal, service: DirectoryService = _service
) -> dict[str, Any]:
    """El perfil de quien llama; 404 hasta que la sincronización lo traiga."""

    return detail(await service.my_employee(principal))


@router.get("/employees/birthdays", response_model=BirthdaysResponse)
@inject
async def birthdays(
    month: int | None = Query(None, ge=1, le=12),
    _: Principal = _principal,
    service: DirectoryService = _service,
) -> dict[str, Any]:
    """Cumpleaños del mes (el actual por defecto), por día."""

    chosen, items = await service.birthdays(month)
    return {
        "month": chosen,
        "items": [{**summary(item), "birth_day": item.birth_date.day} for item in items],
    }


@router.get("/employees/new-hires", response_model=ItemsResponse[NewHireSummary])
@inject
async def new_hires(
    limit: int = Query(6, ge=1, le=24),
    _: Principal = _principal,
    service: DirectoryService = _service,
) -> dict[str, Any]:
    """Nuevos ingresos, del más reciente al más antiguo."""

    items = await service.new_hires(limit)
    return {"items": [{**summary(item), "hire_date": item.hire_date} for item in items]}


@router.get("/employees/{employee_id}", response_model=EmployeeDetail)
@inject
async def get_employee(
    employee_id: UUID,
    principal: Principal = _principal,
    service: DirectoryService = _service,
) -> dict[str, Any]:
    """El perfil de un empleado del directorio público."""

    return detail(await service.get_employee(employee_id, admin=principal.is_platform_admin))


@router.get("/employees/{employee_id}/team", response_model=ItemsResponse[EmployeeSummary])
@inject
async def team(
    employee_id: UUID,
    limit: int = Query(3, ge=1, le=24),
    _: Principal = _principal,
    service: DirectoryService = _service,
) -> dict[str, Any]:
    """Compañeros del mismo departamento."""

    return {"items": [summary(item) for item in await service.team(employee_id, limit)]}


@router.get(
    "/employees/{employee_id}/photo",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}, 304: {"description": "Sin cambios"}},
)
@inject
async def photo(
    employee_id: UUID,
    size: int = Query(96, description="96 o 432"),
    v: str | None = Query(None, max_length=40),
    if_none_match: str | None = Header(None),
    principal: Principal = _principal,
    service: DirectoryService = _service,
) -> Response:
    """Foto de 96 o 432 px. Con ``v`` (``photo_version``) el navegador la guarda un año."""

    image = await service.photo(employee_id, size, admin=principal.is_platform_admin)
    etag = f'"{image.version}-{size}"'
    headers = {"ETag": etag, "Cache-Control": _IMMUTABLE if v else _REVALIDATE}
    if if_none_match and etag in {tag.strip() for tag in if_none_match.split(",")}:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=image.content, media_type=image.content_type, headers=headers)


@router.get("/sites", response_model=ItemsResponse[SiteResponse])
@inject
async def list_sites(
    _: Principal = _principal, service: DirectoryService = _service
) -> dict[str, Any]:
    """Sedes activas con cuántos empleados del directorio público tienen."""

    return {"items": [site(item) for item in await service.list_sites()]}
