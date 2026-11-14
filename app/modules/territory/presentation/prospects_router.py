"""Commercial map endpoints (``/territory``): permissions and prospects.

Everything needs ``read`` of ``mapa-comercial`` except ``/permissions``, which answers
``false`` instead of 403. Loading the monthly file and editing a prospect need ``manage``.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.territory.application.prospects import ProspectService
from app.modules.territory.domain.entities import (
    ImportReport,
    MapPoint,
    Prospect,
    ProspectFilters,
)
from app.modules.territory.domain.vocabulary import MODULE_KEY

router = APIRouter()
_READ = Depends(require_permission(MODULE_KEY))
_principal = Depends(get_current_principal)
_service = Depends(Provide[ApplicationContainer.v1_container.territory_prospect_service])

Quality = Literal["exact", "approximate", "manual", "missing"]
AreaStatus = Literal["inside", "outside", "unlocated"]
VisitStatus = Literal["pending", "visited", "interested", "discarded"]


class PermissionsResponse(BaseModel):
    can_read: bool
    can_manage: bool


class PointGeometry(BaseModel):
    type: Literal["Point"] = "Point"
    # ``[longitude, latitude]``.
    coordinates: list[float]


class MapProperties(BaseModel):
    id: UUID
    external_id: str
    name: str
    category: str | None
    source_zone: str
    sector: str | None
    is_former_customer: bool
    location_quality: Quality
    commercial_zone_id: UUID | None
    visit_status: VisitStatus


class MapFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: UUID
    geometry: PointGeometry
    properties: MapProperties


class MapResponse(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[MapFeature]


class ZoneRefResponse(BaseModel):
    id: UUID
    name: str
    color: str | None


class PhoneResponse(BaseModel):
    number: str
    display: str
    label: str | None
    # A Colombian mobile: the UI offers WhatsApp (``https://wa.me/57<number>``).
    is_mobile: bool


class ContactRawResponse(BaseModel):
    phone: str | None
    email: str | None


class ProspectSummaryResponse(BaseModel):
    id: UUID
    external_id: str
    name: str
    category: str | None
    source_zone: str
    sector: str | None
    address: str | None
    is_former_customer: bool
    location_quality: Quality
    latitude: float | None
    longitude: float | None
    commercial_zone: ZoneRefResponse | None
    visit_status: VisitStatus


class ProspectResponse(ProspectSummaryResponse):
    area_status: AreaStatus | None
    last_purchase_month: date | None
    phones: list[PhoneResponse]
    emails: list[str]
    website: str | None
    observation: str | None
    contact_raw: ContactRawResponse
    visit_note: str | None
    visited_at: datetime | None
    load_id: UUID | None
    created_at: datetime
    updated_at: datetime


class ProspectPageResponse(BaseModel):
    items: list[ProspectSummaryResponse]
    total: int
    page: int
    size: int


class FacetResponse(BaseModel):
    value: str
    count: int


class FacetsResponse(BaseModel):
    source_zones: list[FacetResponse]
    categories: list[FacetResponse]
    total: int
    located: int
    unlocated: int


class ProspectPatch(BaseModel):
    """Every field is optional; latitude and longitude go together."""

    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    visit_status: VisitStatus | None = None
    visit_note: str | None = Field(default=None, max_length=2000)


class SheetResponse(BaseModel):
    name: str
    rows: int
    with_location: int
    without_location: int


class RowIssueResponse(BaseModel):
    sheet: str
    row_number: int
    field: str | None
    message: str


class ImportResponse(BaseModel):
    applied: bool
    blocked: bool
    load_id: UUID | None
    rows_read: int
    with_location: int
    without_location: int
    approximate: int
    to_create: int
    to_update: int
    unchanged: int
    sheets: list[SheetResponse]
    errors: list[RowIssueResponse]
    errors_truncated: bool


class LoadResponse(BaseModel):
    id: UUID
    file_name: str
    rows_read: int
    created: int
    updated: int
    unchanged: int
    loaded_by: str | None
    loaded_at: datetime


def filters(
    source_zone: str | None = Query(default=None, max_length=120),
    category: str | None = Query(default=None, max_length=120),
    zone_id: UUID | None = Query(default=None, description="Commercial zone"),  # noqa: B008
    former: bool | None = Query(default=None, description="Former customers only (or not)"),
    visit_status: VisitStatus | None = Query(default=None),  # noqa: B008
    q: str | None = Query(default=None, max_length=100, description="Name, address or ID"),
) -> ProspectFilters:
    """Filters shared by the map and the list."""

    return ProspectFilters(
        source_zone=source_zone,
        category=category,
        zone_id=zone_id,
        former=former,
        visit_status=visit_status,
        q=q,
    )


_FILTERS = Depends(filters)


def _feature(point: MapPoint) -> MapFeature:
    return MapFeature(
        id=point.id,
        geometry=PointGeometry(coordinates=[point.location.longitude, point.location.latitude]),
        properties=MapProperties(
            id=point.id,
            external_id=point.external_id,
            name=point.name,
            category=point.category,
            source_zone=point.source_zone,
            sector=point.sector,
            is_former_customer=point.is_former_customer,
            location_quality=point.location_quality,  # type: ignore[arg-type]
            commercial_zone_id=point.commercial_zone_id,
            visit_status=point.visit_status,  # type: ignore[arg-type]
        ),
    )


def _summary_fields(prospect: Prospect) -> dict[str, object]:
    data = prospect.data
    zone = prospect.commercial_zone
    return {
        "id": prospect.id,
        "external_id": data.external_id,
        "name": data.name,
        "category": data.category,
        "source_zone": data.source_zone,
        "sector": data.sector,
        "address": data.address,
        "is_former_customer": data.is_former_customer,
        "location_quality": data.location_quality,
        "latitude": data.location.latitude if data.location else None,
        "longitude": data.location.longitude if data.location else None,
        "commercial_zone": (
            ZoneRefResponse(id=zone.id, name=zone.name, color=zone.color) if zone else None
        ),
        "visit_status": prospect.visit_status,
    }


def prospect_response(prospect: Prospect) -> ProspectResponse:
    """The card of a prospect."""

    data = prospect.data
    return ProspectResponse(
        **_summary_fields(prospect),
        area_status=data.area_status,
        last_purchase_month=data.last_purchase_month,
        phones=[PhoneResponse(**asdict(phone)) for phone in data.phones],
        emails=list(data.emails),
        website=data.website,
        observation=data.observation,
        contact_raw=ContactRawResponse(phone=data.phone_raw, email=data.email_raw),
        visit_note=prospect.visit_note,
        visited_at=prospect.visited_at,
        load_id=prospect.load_id,
        created_at=prospect.created_at,
        updated_at=prospect.updated_at,
    )


def _import(report: ImportReport) -> ImportResponse:
    return ImportResponse.model_validate(report, from_attributes=True)


@router.get("/permissions", response_model=PermissionsResponse, summary="What the caller may do")
@inject
async def permissions(
    principal: Principal = _principal, service: ProspectService = _service
) -> PermissionsResponse:
    """Si quien llama ve el mapa comercial y si lo gestiona (importar, zonas, rutas)."""

    can_read, can_manage = await service.permissions(principal)
    return PermissionsResponse(can_read=can_read, can_manage=can_manage)


@router.get(
    "/prospects/map",
    dependencies=[_READ],
    response_model=MapResponse,
    summary="Located prospects as GeoJSON",
)
@inject
async def prospects_map(
    prospect_filters: ProspectFilters = _FILTERS,
    _: Principal = _principal,
    service: ProspectService = _service,
) -> MapResponse:
    """Los prospectos con ubicación, como `FeatureCollection` de puntos `[lon, lat]`."""

    points = await service.map_points(prospect_filters)
    return MapResponse(features=[_feature(point) for point in points])


@router.get(
    "/prospects/facets",
    dependencies=[_READ],
    response_model=FacetsResponse,
    summary="Values of the filters",
)
@inject
async def prospects_facets(
    _: Principal = _principal, service: ProspectService = _service
) -> FacetsResponse:
    """Zonas de origen y categorías con su conteo, y cuántos tienen ubicación."""

    return FacetsResponse.model_validate(await service.facets(), from_attributes=True)


@router.get(
    "/prospects/loads",
    dependencies=[_READ],
    response_model=list[LoadResponse],
    summary="Latest loads of the monthly file",
)
@inject
async def prospect_loads(
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = _principal,
    service: ProspectService = _service,
) -> list[LoadResponse]:
    """Últimas cargas del archivo de prospectos (gestores)."""

    return [
        LoadResponse.model_validate(load, from_attributes=True)
        for load in await service.loads(principal, limit)
    ]


@router.post(
    "/prospects/import",
    dependencies=[_READ],
    response_model=ImportResponse,
    summary="Load the monthly prospects file",
    description=(
        "multipart/form-data with `file` (.xlsx, one sheet per zone). A simulation unless "
        "`apply=true`; all or nothing: one row error blocks it. Creates or updates by the "
        "file's `ID` and never deactivates prospects the file no longer brings. A point moved "
        "by hand and the visit results are kept."
    ),
)
@inject
async def import_prospects(
    file: UploadFile = File(...),  # noqa: B008
    apply: bool = Query(default=False),
    principal: Principal = _principal,
    service: ProspectService = _service,
) -> ImportResponse:
    """Simula el archivo mensual y, con `apply` y sin errores, lo guarda (gestores)."""

    content = await file.read(service.max_upload_bytes + 1)
    report = await service.load(principal, file.filename or "archivo.xlsx", content, apply=apply)
    return _import(report)


@router.get(
    "/prospects",
    dependencies=[_READ],
    response_model=ProspectPageResponse,
    summary="List prospects",
)
@inject
async def list_prospects(
    prospect_filters: ProspectFilters = _FILTERS,
    located: bool | None = Query(default=None, description="false: the ones without location"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    _: Principal = _principal,
    service: ProspectService = _service,
) -> ProspectPageResponse:
    """Prospectos por nombre, con o sin ubicación (`located=false` son los «por ubicar»)."""

    result = await service.page(replace(prospect_filters, located=located), page, size)
    return ProspectPageResponse(
        items=[ProspectSummaryResponse(**_summary_fields(item)) for item in result.items],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.get(
    "/prospects/{prospect_id}",
    dependencies=[_READ],
    response_model=ProspectResponse,
    summary="A prospect's card",
)
@inject
async def get_prospect(
    prospect_id: UUID, _: Principal = _principal, service: ProspectService = _service
) -> ProspectResponse:
    """Todo lo de un prospecto: contacto limpio y original, zona y resultado de visita."""

    return prospect_response(await service.get(prospect_id))


@router.patch(
    "/prospects/{prospect_id}",
    dependencies=[_READ],
    response_model=ProspectResponse,
    summary="Move a prospect or record a visit",
)
@inject
async def patch_prospect(
    prospect_id: UUID,
    payload: ProspectPatch,
    principal: Principal = _principal,
    service: ProspectService = _service,
) -> ProspectResponse:
    """Corrige el punto (queda `manual` y cambia de zona) o marca la visita (gestores)."""

    prospect = await service.update(
        principal,
        prospect_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
        visit_status=payload.visit_status,
        visit_note=payload.visit_note,
        set_note="visit_note" in payload.model_fields_set,
    )
    return prospect_response(prospect)


__all__ = ["prospect_response", "router"]
