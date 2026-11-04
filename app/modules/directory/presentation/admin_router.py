"""Directory administration (``/directory/admin``): platform administrators only."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Response, status

from app.app_container import ApplicationContainer
from app.modules.directory.application.service import (
    MAX_HISTORY,
    MAX_PAGE_SIZE,
    DirectoryService,
)
from app.modules.directory.presentation.schemas import (
    AdminEmployee,
    EmployeeChanges,
    ItemsResponse,
    NewEmployee,
    NewSite,
    PageResponse,
    SiteChanges,
    SiteResponse,
    SyncRequested,
    SyncRunResponse,
    SyncStatusResponse,
    admin_detail,
    site,
    sync_run,
    sync_status,
)
from app.modules.identity.infrastructure.models import UserModel
from app.modules.identity.presentation.router import require_admin

router = APIRouter()
_service = Depends(Provide[ApplicationContainer.v1_container.directory_service])
_admin = Depends(require_admin)


@router.get("/employees", response_model=PageResponse[AdminEmployee])
@inject
async def list_employees(
    q: str | None = Query(None, max_length=200),
    site_id: UUID | None = None,
    department: str | None = Query(None, max_length=200),
    include_hidden: bool = True,
    include_inactive: bool = False,
    page: int = Query(1, ge=1),
    size: int = Query(24, ge=1, le=MAX_PAGE_SIZE),
    _: UserModel = _admin,
    service: DirectoryService = _service,
) -> dict[str, Any]:
    """Todo el directorio, también ocultos (y con ``include_inactive``, inactivos)."""

    items, total = await service.list_employees(
        q,
        site_id,
        department,
        page,
        size,
        include_hidden=include_hidden,
        include_inactive=include_inactive,
    )
    return {
        "items": [admin_detail(item) for item in items],
        "total": total,
        "page": page,
        "size": size,
    }


@router.post("/employees", response_model=AdminEmployee, status_code=status.HTTP_201_CREATED)
@inject
async def create_employee(
    body: NewEmployee, _: UserModel = _admin, service: DirectoryService = _service
) -> dict[str, Any]:
    """Agrega una persona que no está en Entra (sin usuario de la plataforma)."""

    return admin_detail(await service.create_employee(body.as_changes()))


@router.get("/employees/{employee_id}", response_model=AdminEmployee)
@inject
async def get_employee(
    employee_id: UUID, _: UserModel = _admin, service: DirectoryService = _service
) -> dict[str, Any]:
    """Un empleado con sus campos de administración."""

    return admin_detail(await service.get_employee(employee_id, admin=True))


@router.patch("/employees/{employee_id}", response_model=AdminEmployee)
@inject
async def update_employee(
    employee_id: UUID,
    body: EmployeeChanges,
    _: UserModel = _admin,
    service: DirectoryService = _service,
) -> dict[str, Any]:
    """Cambia los campos enviados; en un empleado de Entra ignora los que trae Entra."""

    return admin_detail(await service.update_employee(employee_id, body.as_changes()))


@router.delete("/employees/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_employee(
    employee_id: UUID, _: UserModel = _admin, service: DirectoryService = _service
) -> Response:
    """Elimina un empleado agregado a mano; 409 si viene de Entra."""

    await service.delete_employee(employee_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sync", response_model=SyncRequested, status_code=status.HTTP_202_ACCEPTED)
@inject
async def request_sync(
    admin: UserModel = _admin, service: DirectoryService = _service
) -> dict[str, UUID]:
    """Sincroniza con Entra ya; 409 si está apagada o ya hay una en curso."""

    return {"run_id": await service.request_sync(admin.email or admin.name)}


@router.get("/sync", response_model=SyncStatusResponse)
@inject
async def get_sync_status(
    _: UserModel = _admin, service: DirectoryService = _service
) -> dict[str, Any]:
    """Si la sincronización está encendida, cuándo corre y cómo terminó la última."""

    return sync_status(await service.sync_status())


@router.get("/sync/runs", response_model=ItemsResponse[SyncRunResponse])
@inject
async def sync_history(
    limit: int = Query(20, ge=1, le=MAX_HISTORY),
    _: UserModel = _admin,
    service: DirectoryService = _service,
) -> dict[str, Any]:
    """Historial de sincronizaciones, de la más reciente a la más antigua."""

    return {"items": [sync_run(run) for run in await service.sync_history(limit)]}


@router.get("/sites", response_model=ItemsResponse[SiteResponse])
@inject
async def list_sites(
    include_inactive: bool = True, _: UserModel = _admin, service: DirectoryService = _service
) -> dict[str, Any]:
    """Todas las sedes, también las inactivas."""

    return {"items": [site(item) for item in await service.list_sites(include_inactive)]}


@router.post("/sites", response_model=SiteResponse, status_code=status.HTTP_201_CREATED)
@inject
async def create_site(
    body: NewSite, _: UserModel = _admin, service: DirectoryService = _service
) -> dict[str, Any]:
    """Crea una sede; 409 si el nombre existe."""

    return site(await service.create_site(body.name, body.address, body.is_active))


@router.patch("/sites/{site_id}", response_model=SiteResponse)
@inject
async def update_site(
    site_id: UUID, body: SiteChanges, _: UserModel = _admin, service: DirectoryService = _service
) -> dict[str, Any]:
    """Cambia los campos enviados de una sede."""

    return site(await service.update_site(site_id, body.as_changes()))


@router.delete("/sites/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_site(
    site_id: UUID, _: UserModel = _admin, service: DirectoryService = _service
) -> Response:
    """Elimina una sede; 409 mientras tenga empleados."""

    await service.delete_site(site_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
