"""Zones of the commercial map (``/territory/zones``).

Reading needs ``read`` of ``mapa-comercial``; creating, changing and deleting commercial zones
needs ``manage``. Official zones are loaded with ``scripts/import-geo-zones.py``.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.territory.application.zones import ZoneService
from app.modules.territory.domain.entities import Zone
from app.modules.territory.domain.vocabulary import MODULE_KEY

router = APIRouter()
_READ = Depends(require_permission(MODULE_KEY))
_principal = Depends(get_current_principal)
_service = Depends(Provide[ApplicationContainer.v1_container.territory_zone_service])

Layer = Literal["commercial", "official"]
Level = Literal["department", "municipality", "commune", "neighborhood", "custom"]
Color = Literal["gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red"]


class ZoneProperties(BaseModel):
    id: UUID
    layer: Layer
    level: Level
    name: str
    code: str | None
    parent_id: UUID | None
    color: Color | None
    source: str | None
    # Prospects inside (commercial zones only; 0 for official ones).
    prospect_count: int


class ZoneFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: UUID
    # GeoJSON ``MultiPolygon``.
    geometry: dict[str, Any]
    properties: ZoneProperties


class ZoneCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[ZoneFeature]


class ZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    color: Color
    # GeoJSON ``Polygon`` or ``MultiPolygon``, ``[lon, lat]``.
    geometry: dict[str, Any]


class ZoneAdopt(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    color: Color
    official_zone_ids: list[UUID] = Field(min_length=1, max_length=200)


class ZoneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    color: Color | None = None
    geometry: dict[str, Any] | None = None


def _feature(zone: Zone) -> ZoneFeature:
    return ZoneFeature(
        id=zone.id,
        geometry=zone.geometry,
        properties=ZoneProperties(
            id=zone.id,
            layer=zone.layer,  # type: ignore[arg-type]
            level=zone.level,  # type: ignore[arg-type]
            name=zone.name,
            code=zone.code,
            parent_id=zone.parent_id,
            color=zone.color,  # type: ignore[arg-type]
            source=zone.source,
            prospect_count=zone.prospect_count,
        ),
    )


@router.get(
    "/zones", dependencies=[_READ], response_model=ZoneCollection, summary="Zones of a layer"
)
@inject
async def list_zones(
    layer: Layer = Query(default="commercial"),  # noqa: B008
    level: Level | None = Query(default=None),  # noqa: B008
    simplify: bool = Query(default=True, description="Lighter geometry, for painting"),
    _: Principal = _principal,
    service: ZoneService = _service,
) -> ZoneCollection:
    """Zonas comerciales (con su conteo de prospectos) u oficiales, como `FeatureCollection`."""

    zones = await service.list(layer, level, simplify)
    return ZoneCollection(features=[_feature(zone) for zone in zones])


@router.post(
    "/zones/adopt",
    dependencies=[_READ],
    response_model=ZoneFeature,
    status_code=status.HTTP_201_CREATED,
    summary="Commercial zone joining official ones",
)
@inject
async def adopt_zone(
    payload: ZoneAdopt, principal: Principal = _principal, service: ZoneService = _service
) -> ZoneFeature:
    """Crea una zona comercial uniendo zonas oficiales (p. ej. Laureles = comuna 11)."""

    zone = await service.adopt(principal, payload.name, payload.color, payload.official_zone_ids)
    return _feature(zone)


@router.post(
    "/zones",
    dependencies=[_READ],
    response_model=ZoneFeature,
    status_code=status.HTTP_201_CREATED,
    summary="Draw a commercial zone",
)
@inject
async def create_zone(
    payload: ZoneCreate, principal: Principal = _principal, service: ZoneService = _service
) -> ZoneFeature:
    """Crea una zona comercial dibujada; sus prospectos quedan asignados a ella (gestores)."""

    zone = await service.create(principal, payload.name, payload.color, payload.geometry)
    return _feature(zone)


@router.get("/zones/{zone_id}", dependencies=[_READ], response_model=ZoneFeature, summary="A zone")
@inject
async def get_zone(
    zone_id: UUID, _: Principal = _principal, service: ZoneService = _service
) -> ZoneFeature:
    """Una zona con su geometría completa."""

    return _feature(await service.get(zone_id))


@router.put(
    "/zones/{zone_id}",
    dependencies=[_READ],
    response_model=ZoneFeature,
    summary="Change a commercial zone",
)
@inject
async def update_zone(
    zone_id: UUID,
    payload: ZoneUpdate,
    principal: Principal = _principal,
    service: ZoneService = _service,
) -> ZoneFeature:
    """Renombra, cambia el color o la forma; una forma nueva reasigna prospectos (gestores)."""

    zone = await service.update(
        principal, zone_id, name=payload.name, color=payload.color, geometry=payload.geometry
    )
    return _feature(zone)


@router.delete(
    "/zones/{zone_id}",
    dependencies=[_READ],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a commercial zone",
)
@inject
async def delete_zone(
    zone_id: UUID, principal: Principal = _principal, service: ZoneService = _service
) -> Response:
    """Borra una zona comercial; sus prospectos pasan a la zona que los cubra (gestores)."""

    await service.delete(principal, zone_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
