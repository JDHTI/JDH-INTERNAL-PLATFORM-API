"""HTTP endpoints for areas, their role catalog and their members.

Reading is open to anyone with access to the area (member, responsible, moderator
or platform administrator); every write requires a platform administrator. The
grants of each role (``/roles/{role_id}/permissions``) decide what members may read
or manage in each module of the area.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, EmailStr, Field

from app.app_container import ApplicationContainer
from app.modules.areas.application.dto import (
    AreaView,
    CreateAreaCommand,
    MemberView,
    UpdateAreaCommand,
)
from app.modules.areas.application.service import AreaService
from app.modules.areas.domain.entities import AreaRole
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal

router = APIRouter()

_principal = Depends(get_current_principal)
_service = Depends(Provide[ApplicationContainer.v1_container.area_service])


# --- Schemas ---


class UserRefResponse(BaseModel):
    id: UUID
    email: str | None
    display_name: str | None


class RoleRefResponse(BaseModel):
    id: UUID
    name: str


class AreaResponse(BaseModel):
    id: UUID
    slug: str
    name: str
    remit: str | None
    icon: str | None
    sort_order: int
    responsible: UserRefResponse | None
    my_role: RoleRefResponse | None
    is_responsible: bool
    created_at: datetime
    updated_at: datetime
    # Module key -> read | manage the caller holds in this area (the menu hides the rest).
    my_modules: dict[str, str] = {}


class GrantRequest(BaseModel):
    module: str = Field(min_length=1, max_length=60, examples=["pqrsf", "*"])
    action: str = Field(pattern="^(read|manage)$")


class RolePermissionsRequest(BaseModel):
    """The full set of grants of the role; ``*`` is the whole area."""

    grants: list[GrantRequest] = Field(max_length=100)


class RolePermissionsResponse(BaseModel):
    role: RoleRefResponse
    grants: list[GrantRequest]


class AreaCreateRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=50, examples=["talento-humano"])
    name: str = Field(min_length=1, max_length=150)
    remit: str | None = Field(default=None, max_length=300)
    icon: str | None = Field(default=None, max_length=64, examples=["Users"])
    sort_order: int = 0
    responsible_email: EmailStr | None = None


class AreaUpdateRequest(BaseModel):
    """The slug is immutable. ``responsible_email: null`` removes the responsible."""

    name: str | None = Field(default=None, min_length=1, max_length=150)
    remit: str | None = Field(default=None, max_length=300)
    icon: str | None = Field(default=None, max_length=64)
    sort_order: int | None = None
    responsible_email: EmailStr | None = None


class RoleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class RoleResponse(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    updated_at: datetime


class MemberCreateRequest(BaseModel):
    email: EmailStr
    role_id: UUID


class MemberUpdateRequest(BaseModel):
    role_id: UUID


class MemberResponse(BaseModel):
    id: UUID
    user: UserRefResponse
    role: RoleRefResponse
    created_at: datetime
    updated_at: datetime


def _area(view: AreaView) -> AreaResponse:
    return AreaResponse.model_validate(view, from_attributes=True)


def _member(view: MemberView) -> MemberResponse:
    return MemberResponse.model_validate(view, from_attributes=True)


def _role(role: AreaRole) -> RoleResponse:
    return RoleResponse.model_validate(role, from_attributes=True)


# --- Areas ---


@router.get("", response_model=list[AreaResponse], summary="Areas visible to the caller")
@inject
async def list_areas(
    principal: Principal = _principal, service: AreaService = _service
) -> list[AreaResponse]:
    return [_area(v) for v in await service.list_areas(principal)]


@router.post(
    "", response_model=AreaResponse, status_code=status.HTTP_201_CREATED, summary="Create area"
)
@inject
async def create_area(
    payload: AreaCreateRequest, principal: Principal = _principal, service: AreaService = _service
) -> AreaResponse:
    command = CreateAreaCommand(**payload.model_dump())
    return _area(await service.create_area(command, principal))


@router.get("/{slug}", response_model=AreaResponse, summary="Area detail")
@inject
async def get_area(
    slug: str, principal: Principal = _principal, service: AreaService = _service
) -> AreaResponse:
    return _area(await service.get_area(slug, principal))


@router.patch("/{slug}", response_model=AreaResponse, summary="Update area")
@inject
async def update_area(
    slug: str,
    payload: AreaUpdateRequest,
    principal: Principal = _principal,
    service: AreaService = _service,
) -> AreaResponse:
    command = UpdateAreaCommand(fields=frozenset(payload.model_fields_set), **payload.model_dump())
    return _area(await service.update_area(slug, command, principal))


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete area")
@inject
async def delete_area(
    slug: str, principal: Principal = _principal, service: AreaService = _service
) -> None:
    await service.delete_area(slug, principal)


# --- Roles ---


@router.get("/{slug}/roles", response_model=list[RoleResponse], summary="Area role catalog")
@inject
async def list_roles(
    slug: str, principal: Principal = _principal, service: AreaService = _service
) -> list[RoleResponse]:
    return [_role(r) for r in await service.list_roles(slug, principal)]


@router.post(
    "/{slug}/roles",
    response_model=RoleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add role",
)
@inject
async def create_role(
    slug: str,
    payload: RoleRequest,
    principal: Principal = _principal,
    service: AreaService = _service,
) -> RoleResponse:
    return _role(await service.create_role(slug, payload.name, principal))


@router.patch("/{slug}/roles/{role_id}", response_model=RoleResponse, summary="Rename role")
@inject
async def rename_role(
    slug: str,
    role_id: UUID,
    payload: RoleRequest,
    principal: Principal = _principal,
    service: AreaService = _service,
) -> RoleResponse:
    return _role(await service.rename_role(slug, role_id, payload.name, principal))


@router.delete(
    "/{slug}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete role"
)
@inject
async def delete_role(
    slug: str, role_id: UUID, principal: Principal = _principal, service: AreaService = _service
) -> None:
    await service.delete_role(slug, role_id, principal)


@router.get(
    "/{slug}/roles/{role_id}/permissions",
    response_model=RolePermissionsResponse,
    summary="Grants of a role",
)
@inject
async def get_role_permissions(
    slug: str, role_id: UUID, principal: Principal = _principal, service: AreaService = _service
) -> dict:
    """Permisos de un rol del área (administradores y moderadores)."""

    role, grants = await service.get_role_permissions(slug, role_id, principal)
    return {
        "role": {"id": role.id, "name": role.name},
        "grants": [{"module": g.module, "action": g.action} for g in grants],
    }


@router.put(
    "/{slug}/roles/{role_id}/permissions",
    response_model=RolePermissionsResponse,
    summary="Replace the grants of a role",
)
@inject
async def set_role_permissions(
    slug: str,
    role_id: UUID,
    payload: RolePermissionsRequest,
    principal: Principal = _principal,
    service: AreaService = _service,
) -> dict:
    """Reemplaza los permisos del rol; 422 si un módulo no existe en el área."""

    role, grants = await service.set_role_permissions(
        slug, role_id, [(g.module, g.action) for g in payload.grants], principal
    )
    return {
        "role": {"id": role.id, "name": role.name},
        "grants": [{"module": g.module, "action": g.action} for g in grants],
    }


# --- Members ---


@router.get("/{slug}/members", response_model=list[MemberResponse], summary="Area members")
@inject
async def list_members(
    slug: str, principal: Principal = _principal, service: AreaService = _service
) -> list[MemberResponse]:
    return [_member(m) for m in await service.list_members(slug, principal)]


@router.post(
    "/{slug}/members",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add member",
)
@inject
async def add_member(
    slug: str,
    payload: MemberCreateRequest,
    principal: Principal = _principal,
    service: AreaService = _service,
) -> MemberResponse:
    return _member(await service.add_member(slug, payload.email, payload.role_id, principal))


@router.patch(
    "/{slug}/members/{member_id}", response_model=MemberResponse, summary="Change member role"
)
@inject
async def change_member_role(
    slug: str,
    member_id: UUID,
    payload: MemberUpdateRequest,
    principal: Principal = _principal,
    service: AreaService = _service,
) -> MemberResponse:
    return _member(await service.change_member_role(slug, member_id, payload.role_id, principal))


@router.delete(
    "/{slug}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove member",
)
@inject
async def remove_member(
    slug: str, member_id: UUID, principal: Principal = _principal, service: AreaService = _service
) -> None:
    await service.remove_member(slug, member_id, principal)


__all__ = ["router"]
