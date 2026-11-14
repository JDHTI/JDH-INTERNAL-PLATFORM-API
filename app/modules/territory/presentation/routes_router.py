"""Visit routes of the commercial map (``/territory/routes``).

Reading needs ``read`` of ``mapa-comercial``; planning, changing and deleting need ``manage``.
The route's assignee may record the result of each visit. Creating and recomputing a route
spend routing quota, so they are rate limited as ``strict``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, EmailStr, Field

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.domain.principal import DirectoryUser, Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.territory.application.routes import RouteService
from app.modules.territory.domain.entities import Point, Route, RouteStop
from app.modules.territory.domain.vocabulary import MODULE_KEY, PENDING

router = APIRouter()
_READ = Depends(require_permission(MODULE_KEY))
_principal = Depends(get_current_principal)
_service = Depends(Provide[ApplicationContainer.v1_container.territory_route_service])

GeometryStatus = Literal["road", "straight", "pending"]
VisitStatus = Literal["pending", "visited", "interested", "discarded"]


class UserRef(BaseModel):
    id: UUID
    email: str | None
    display_name: str | None


class OriginBody(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    label: str | None = Field(default=None, max_length=160)


class StopResponse(BaseModel):
    id: UUID
    position: int
    prospect_id: UUID | None
    name: str
    address: str | None
    latitude: float
    longitude: float
    visit_status: VisitStatus
    visit_note: str | None
    visited_at: datetime | None
    # The leg that arrives at this stop; null for the first stop of a route without origin.
    leg_distance_m: int | None
    leg_duration_s: int | None


class RouteSummaryResponse(BaseModel):
    id: UUID
    name: str
    planned_for: date | None
    assignee: UserRef | None
    stop_count: int
    visited_count: int
    distance_m: int | None
    duration_s: int | None
    geometry_status: GeometryStatus
    created_by: UserRef | None
    created_at: datetime
    updated_at: datetime


class RouteResponse(RouteSummaryResponse):
    origin: OriginBody | None
    # GeoJSON ``LineString``; null while ``pending``.
    geometry: dict[str, Any] | None
    # Why it is not by road, when it is not.
    routing_note: str | None
    stops: list[StopResponse]


class RoutePageResponse(BaseModel):
    items: list[RouteSummaryResponse]
    total: int
    page: int
    size: int


class RouteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    planned_for: date | None = None
    assignee_email: EmailStr | None = None
    origin: OriginBody | None = None
    prospect_ids: list[UUID] = Field(min_length=2, max_length=200)
    optimize: bool = True


class RouteUpdate(BaseModel):
    """Only the fields sent change; ``null`` clears the date, the assignee or the origin."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    planned_for: date | None = None
    assignee_email: EmailStr | None = None
    origin: OriginBody | None = None
    prospect_ids: list[UUID] | None = Field(default=None, min_length=2, max_length=200)


class ComputeRequest(BaseModel):
    optimize: bool = False


class StopPatch(BaseModel):
    visit_status: VisitStatus | None = None
    visit_note: str | None = Field(default=None, max_length=2000)


def _user(people: dict[UUID, DirectoryUser], user_id: UUID | None) -> UserRef | None:
    person = people.get(user_id) if user_id else None
    if person is None:
        return None
    return UserRef(id=person.id, email=person.email, display_name=person.display_name)


def _stop(stop: RouteStop) -> StopResponse:
    data = stop.data
    return StopResponse(
        id=stop.id,
        position=stop.position,
        prospect_id=data.prospect_id,
        name=data.name,
        address=data.address,
        latitude=data.location.latitude,
        longitude=data.location.longitude,
        visit_status=data.visit_status,  # type: ignore[arg-type]
        visit_note=data.visit_note,
        visited_at=data.visited_at,
        leg_distance_m=data.leg_distance_m,
        leg_duration_s=data.leg_duration_s,
    )


async def _route(route: Route, service: RouteService) -> RouteResponse:
    people = await service.people([route.assignee_id, route.created_by_id])
    return RouteResponse(
        id=route.id,
        name=route.name,
        planned_for=route.planned_for,
        assignee=_user(people, route.assignee_id),
        stop_count=len(route.stops),
        visited_count=sum(1 for stop in route.stops if stop.data.visit_status != PENDING),
        distance_m=route.path.distance_m,
        duration_s=route.path.duration_s,
        geometry_status=route.path.geometry_status,  # type: ignore[arg-type]
        created_by=_user(people, route.created_by_id),
        created_at=route.created_at,
        updated_at=route.updated_at,
        origin=(
            OriginBody(
                latitude=route.origin.latitude,
                longitude=route.origin.longitude,
                label=route.origin_label,
            )
            if route.origin
            else None
        ),
        geometry=route.path.geometry,
        routing_note=route.path.routing_note,
        stops=[_stop(stop) for stop in route.stops],
    )


def _point(origin: OriginBody | None) -> Point | None:
    return Point(latitude=origin.latitude, longitude=origin.longitude) if origin else None


@router.get(
    "/routes", dependencies=[_READ], response_model=RoutePageResponse, summary="Visit routes"
)
@inject
async def list_routes(
    mine: bool = Query(default=False, description="Only the routes assigned to the caller"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    principal: Principal = _principal,
    service: RouteService = _service,
) -> RoutePageResponse:
    """Rutas de visita, las más recientes por fecha planeada primero."""

    result = await service.page(principal, mine, page, size)
    people = await service.people(
        [item.assignee_id for item in result.items] + [item.created_by_id for item in result.items]
    )
    return RoutePageResponse(
        items=[
            RouteSummaryResponse(
                id=item.id,
                name=item.name,
                planned_for=item.planned_for,
                assignee=_user(people, item.assignee_id),
                stop_count=item.stop_count,
                visited_count=item.visited_count,
                distance_m=item.distance_m,
                duration_s=item.duration_s,
                geometry_status=item.geometry_status,  # type: ignore[arg-type]
                created_by=_user(people, item.created_by_id),
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in result.items
        ],
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.post(
    "/routes",
    dependencies=[_READ],
    response_model=RouteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Plan a visit route",
    description=(
        "Between 2 and `ROUTING_MAX_STOPS` located prospects. `optimize` reorders them for the "
        "least time. The road line comes from openrouteservice; when it does not answer, the "
        "route is saved in straight lines (`geometry_status = straight`, with `routing_note`)."
    ),
)
@inject
async def create_route(
    payload: RouteCreate, principal: Principal = _principal, service: RouteService = _service
) -> RouteResponse:
    """Crea la ruta y la calcula por calles (gestores)."""

    route = await service.create(
        principal,
        name=payload.name,
        prospect_ids=payload.prospect_ids,
        planned_for=payload.planned_for,
        assignee_email=payload.assignee_email,
        origin=_point(payload.origin),
        origin_label=payload.origin.label if payload.origin else None,
        optimize=payload.optimize,
    )
    return await _route(route, service)


@router.get(
    "/routes/{route_id}", dependencies=[_READ], response_model=RouteResponse, summary="A route"
)
@inject
async def get_route(
    route_id: UUID, _: Principal = _principal, service: RouteService = _service
) -> RouteResponse:
    """Una ruta con sus paradas en orden y su trazado guardado (no consume cuota)."""

    return await _route(await service.get(route_id), service)


@router.put(
    "/routes/{route_id}",
    dependencies=[_READ],
    response_model=RouteResponse,
    summary="Change a route",
)
@inject
async def update_route(
    route_id: UUID,
    payload: RouteUpdate,
    principal: Principal = _principal,
    service: RouteService = _service,
) -> RouteResponse:
    """Cambia la ruta; paradas u origen nuevos la dejan `pending` hasta recalcular (gestores)."""

    route = await service.update(
        principal,
        route_id,
        name=payload.name,
        planned_for=payload.planned_for,
        assignee_email=payload.assignee_email,
        origin=_point(payload.origin),
        origin_label=payload.origin.label if payload.origin else None,
        prospect_ids=payload.prospect_ids,
        sent=frozenset(payload.model_fields_set),
    )
    return await _route(route, service)


@router.post(
    "/routes/{route_id}/compute",
    dependencies=[_READ],
    response_model=RouteResponse,
    summary="Compute a route again",
)
@inject
async def compute_route(
    route_id: UUID,
    payload: ComputeRequest | None = None,
    principal: Principal = _principal,
    service: RouteService = _service,
) -> RouteResponse:
    """Vuelve a trazar la ruta por calles; con `optimize` también la reordena (gestores)."""

    optimize = payload.optimize if payload else False
    return await _route(await service.compute(principal, route_id, optimize), service)


@router.patch(
    "/routes/{route_id}/stops/{stop_id}",
    dependencies=[_READ],
    response_model=StopResponse,
    summary="Record a visit",
)
@inject
async def patch_stop(
    route_id: UUID,
    stop_id: UUID,
    payload: StopPatch,
    principal: Principal = _principal,
    service: RouteService = _service,
) -> StopResponse:
    """Resultado de la visita en la parada y en el prospecto (gestores o el responsable)."""

    stop = await service.update_stop(
        principal,
        route_id,
        stop_id,
        visit_status=payload.visit_status,
        visit_note=payload.visit_note,
        set_note="visit_note" in payload.model_fields_set,
    )
    return _stop(stop)


@router.delete(
    "/routes/{route_id}",
    dependencies=[_READ],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a route",
)
@inject
async def delete_route(
    route_id: UUID, principal: Principal = _principal, service: RouteService = _service
) -> Response:
    """Borra la ruta con sus paradas (gestores)."""

    await service.delete(principal, route_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
