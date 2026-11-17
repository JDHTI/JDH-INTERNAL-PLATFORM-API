"""Administrator endpoints for the PQRSF site catalog."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.modules.identity.infrastructure.models import UserModel
from app.modules.identity.presentation.router import require_admin
from app.modules.sites.application.service import SiteService
from app.modules.sites.domain.entities import Site

router = APIRouter()
_service = Depends(Provide[ApplicationContainer.v1_container.site_service])


class SiteResponse(BaseModel):
    """Site catalog response."""

    id: UUID
    code: str
    name: str
    # Letters the site puts in PQRSF consecutives (P26AC0001).
    abbreviation: str
    # Printed on the improvement opportunity (P.06): city, address and its code (1181BOG).
    city: str | None = None
    address: str | None = None
    om_code: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SiteCreateRequest(BaseModel):
    """Administrator site creation payload."""

    code: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=150)
    abbreviation: str = Field(min_length=2, max_length=4)
    is_active: bool = True


class SiteUpdateRequest(BaseModel):
    """Administrator site update payload."""

    name: str | None = Field(default=None, min_length=1, max_length=150)
    abbreviation: str | None = Field(default=None, min_length=2, max_length=4)
    city: str | None = Field(default=None, max_length=80)
    address: str | None = Field(default=None, max_length=200)
    om_code: str | None = Field(default=None, max_length=3)
    is_active: bool | None = None


def _response(site: Site) -> SiteResponse:
    return SiteResponse.model_validate(site, from_attributes=True)


@router.get("", response_model=list[SiteResponse], summary="List PQRSF sites")
@inject
async def list_sites(
    include_inactive: bool = False,
    _: UserModel = Depends(require_admin),  # noqa: B008
    service: SiteService = _service,
) -> list[SiteResponse]:
    """List active sites or the full catalog for administrators."""

    return [_response(site) for site in await service.list_sites(include_inactive)]


@router.get("/{site_id}", response_model=SiteResponse, summary="Get PQRSF site")
@inject
async def get_site(
    site_id: UUID,
    _: UserModel = Depends(require_admin),  # noqa: B008
    service: SiteService = _service,
) -> SiteResponse:
    """Return one site."""

    return _response(await service.get_site(site_id))


@router.post(
    "",
    response_model=SiteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create PQRSF site",
)
@inject
async def create_site(
    payload: SiteCreateRequest,
    _: UserModel = Depends(require_admin),  # noqa: B008
    service: SiteService = _service,
) -> SiteResponse:
    """Create a site."""

    return _response(
        await service.create_site(
            payload.code, payload.name, payload.abbreviation, payload.is_active
        )
    )


@router.patch("/{site_id}", response_model=SiteResponse, summary="Update PQRSF site")
@inject
async def update_site(
    site_id: UUID,
    payload: SiteUpdateRequest,
    _: UserModel = Depends(require_admin),  # noqa: B008
    service: SiteService = _service,
) -> SiteResponse:
    """Change a site's display name, abbreviation, OM data or active state.

    ``city``, ``address`` and ``om_code`` are what an improvement opportunity prints;
    ``null`` clears them.
    """

    return _response(
        await service.update_site(
            site_id,
            payload.name,
            payload.is_active,
            frozenset(payload.model_fields_set),
            abbreviation=payload.abbreviation,
            city=payload.city,
            address=payload.address,
            om_code=payload.om_code,
        )
    )


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete PQRSF site")
@inject
async def delete_site(
    site_id: UUID,
    _: UserModel = Depends(require_admin),  # noqa: B008
    service: SiteService = _service,
) -> None:
    """Delete an unused site."""

    await service.delete_site(site_id)


__all__ = ["SiteResponse", "router"]
