"""The permissions panel (``/permissions``): catalog, my permissions, a person's areas, log.

Everyone signed in reads the catalog and their own permissions (the UI builds the
menu from ``/permissions/me``). A person's areas and the log are for administrators
and moderators. Grants are edited on ``/areas/{slug}/roles/{role_id}/permissions``
and members on ``/areas/{slug}/members``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.app_container import ApplicationContainer
from app.modules.areas.application.service import AreaService
from app.modules.areas.domain.catalog import ACTIONS, modules_for
from app.modules.areas.domain.entities import RolePermission
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal

router = APIRouter()
_principal = Depends(get_current_principal)
_service = Depends(Provide[ApplicationContainer.v1_container.area_service])


class ModuleResponse(BaseModel):
    key: str
    label: str


class CatalogAreaResponse(BaseModel):
    slug: str
    name: str
    modules: list[ModuleResponse]


class CatalogResponse(BaseModel):
    """What an area role can grant: per area, its modules; plus ``*`` for the whole area."""

    actions: list[str]
    wildcard: str
    areas: list[CatalogAreaResponse]


class MyAreaResponse(BaseModel):
    area_id: UUID
    slug: str
    name: str
    is_responsible: bool
    role_id: UUID | None
    # The caller holds the area's leader role.
    is_leader: bool = False
    modules: dict[str, str]


class MyPermissionsResponse(BaseModel):
    """The caller's global role and, per area, each module they may read or manage."""

    role: str
    is_platform_admin: bool
    is_moderator: bool
    areas: list[MyAreaResponse]


class GrantResponse(BaseModel):
    module: str
    action: str


class RoleRefResponse(BaseModel):
    id: UUID
    name: str
    is_leader: bool = False


class UserAreaResponse(BaseModel):
    area_id: UUID
    slug: str
    name: str
    is_responsible: bool
    role: RoleRefResponse | None
    grants: list[GrantResponse]


class UserPermissionsResponse(BaseModel):
    user_id: UUID
    areas: list[UserAreaResponse]


class EventResponse(BaseModel):
    id: UUID
    at: datetime
    action: str
    actor_user_id: UUID | None
    area_id: UUID | None
    target_user_id: UUID | None
    role_id: UUID | None
    detail: dict[str, Any]


class EventPage(BaseModel):
    items: list[EventResponse]
    total: int
    offset: int
    limit: int


def _grants(grants: list[RolePermission]) -> list[dict[str, str]]:
    return [{"module": grant.module, "action": grant.action} for grant in grants]


@router.get("/catalog", response_model=CatalogResponse, summary="Grantable modules per area")
@inject
async def catalog(
    principal: Principal = _principal, service: AreaService = _service
) -> dict[str, Any]:
    """Módulos que un rol de cada área puede conceder, y las dos acciones."""

    areas = await service.all_areas()
    return {
        "actions": list(ACTIONS),
        "wildcard": "*",
        "areas": [
            {
                "slug": area.slug,
                "name": area.name,
                "modules": [{"key": m.key, "label": m.label} for m in modules_for(area.slug)],
            }
            for area in areas
        ],
    }


@router.get("/me", response_model=MyPermissionsResponse, summary="The caller's permissions")
@inject
async def my_permissions(
    principal: Principal = _principal, service: AreaService = _service
) -> dict[str, Any]:
    """Rol global y, por área, lo que puedes consultar (read) o gestionar (manage)."""

    views = await service.list_areas(principal)
    profile = await service.profile(principal)
    return {
        "role": principal.role,
        "is_platform_admin": principal.is_platform_admin,
        "is_moderator": principal.is_moderator,
        "areas": [
            {
                "area_id": view.id,
                "slug": view.slug,
                "name": view.name,
                "is_responsible": view.is_responsible,
                "role_id": profile.areas[view.slug].role_id if view.slug in profile.areas else None,
                "is_leader": view.slug in profile.areas and profile.areas[view.slug].is_leader,
                "modules": view.my_modules,
            }
            for view in views
            if view.my_modules
        ],
    }


@router.get("/users/{user_id}", response_model=UserPermissionsResponse, summary="A person's areas")
@inject
async def user_permissions(
    user_id: UUID, principal: Principal = _principal, service: AreaService = _service
) -> dict[str, Any]:
    """Áreas, rol y permisos de una persona (administradores y moderadores)."""

    areas = await service.user_permissions(user_id, principal)
    return {
        "user_id": user_id,
        "areas": [
            {
                "area_id": area.area_id,
                "slug": area.slug,
                "name": area.name,
                "is_responsible": area.is_responsible,
                "role": (
                    {"id": area.role.id, "name": area.role.name, "is_leader": area.role.is_leader}
                    if area.role
                    else None
                ),
                "grants": _grants(area.grants),
            }
            for area in areas
        ],
    }


@router.get("/audit", response_model=EventPage, summary="Permissions log")
@inject
async def audit(
    area_slug: str | None = Query(None, max_length=50),
    target_user_id: UUID | None = None,
    action: str | None = Query(None, max_length=40),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    principal: Principal = _principal,
    service: AreaService = _service,
) -> dict[str, Any]:
    """Bitácora de cambios de permisos, del más reciente al más antiguo."""

    items, total = await service.list_events(
        principal,
        area_slug=area_slug,
        target_user_id=target_user_id,
        action=action,
        offset=offset,
        limit=limit,
    )
    return {
        "items": [
            {
                "id": event.id,
                "at": event.at,
                "action": event.action,
                "actor_user_id": event.actor_user_id,
                "area_id": event.area_id,
                "target_user_id": event.target_user_id,
                "role_id": event.role_id,
                "detail": event.detail,
            }
            for event in items
        ],
        "total": total,
        "offset": offset,
        "limit": limit,
    }
