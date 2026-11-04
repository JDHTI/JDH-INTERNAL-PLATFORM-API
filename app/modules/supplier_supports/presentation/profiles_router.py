"""Endpoints of the supplier support profiles."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.supplier_supports.application.profiles import SupportProfileService
from app.modules.supplier_supports.domain.profiles import DEFAULT_FIELDS, Profile

router = APIRouter(dependencies=[Depends(require_permission("apoyos"))])
_service = Depends(Provide[ApplicationContainer.v1_container.supplier_support_profile_service])
_principal = Depends(get_current_principal)


class ProfileBody(BaseModel):
    """What a person sets on a profile; the whole profile is replaced."""

    model_config = ConfigDict(extra="forbid")

    defaults: dict[str, Any] = Field(
        default_factory=dict,
        description="Values that pre-fill a support: " + ", ".join(DEFAULT_FIELDS),
    )
    column_map: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Field -> headers of the supplier's file, e.g. "
        '{"supplier_value": ["% APOYO DIVERSEY"]}',
    )
    sheet: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=1000)
    version: int | None = Field(
        default=None, ge=1, description="Version being edited; omit it to create the profile"
    )


class ProfileResponse(BaseModel):
    """The profile of a commercial supplier."""

    supplier_code: str
    supplier_name: str
    defaults: dict[str, Any]
    column_map: dict[str, list[str]]
    sheet: str | None
    notes: str | None
    version: int
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


def _response(profile: Profile) -> ProfileResponse:
    return ProfileResponse(
        supplier_code=profile.supplier_code,
        supplier_name=profile.supplier_name,
        defaults=profile.data.defaults,
        column_map=profile.data.column_map,
        sheet=profile.data.sheet,
        notes=profile.data.notes,
        version=profile.version,
        created_by=profile.created_by,
        updated_by=profile.updated_by,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


@router.get("", response_model=list[ProfileResponse], summary="List supplier profiles")
@inject
async def list_profiles(
    _: Principal = _principal, service: SupportProfileService = _service
) -> list[ProfileResponse]:
    """Every supplier that has a profile of supports."""

    return [_response(profile) for profile in await service.list()]


@router.get("/{supplier_code}", response_model=ProfileResponse, summary="Get a supplier's profile")
@inject
async def get_profile(
    supplier_code: str, _: Principal = _principal, service: SupportProfileService = _service
) -> ProfileResponse:
    """The defaults and file column mapping of one commercial supplier (404 if it has none)."""

    return _response(await service.get(supplier_code))


@router.put(
    "/{supplier_code}", response_model=ProfileResponse, summary="Create or replace a profile"
)
@inject
async def save_profile(
    supplier_code: str,
    body: ProfileBody,
    principal: Principal = _principal,
    service: SupportProfileService = _service,
) -> ProfileResponse:
    """Create the profile (no ``version``) or replace it (with the version). Managers only."""

    profile = await service.save(
        principal,
        supplier_code,
        defaults=body.defaults,
        column_map=body.column_map,
        sheet=body.sheet,
        notes=body.notes,
        expected_version=body.version,
    )
    return _response(profile)


@router.delete("/{supplier_code}", status_code=204, summary="Delete a supplier's profile")
@inject
async def delete_profile(
    supplier_code: str,
    principal: Principal = _principal,
    service: SupportProfileService = _service,
) -> Response:
    """Remove a profile; the supports already created are not touched. Managers only."""

    await service.delete(principal, supplier_code)
    return Response(status_code=204)


__all__ = ["router"]
