"""In-memory ports and a workbook builder for the commercial map."""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import openpyxl
import pytest

from app.core.errors import ForbiddenError, NotFoundError
from app.modules.identity.domain.principal import DirectoryUser, Principal
from app.modules.territory.application.prospects import ProspectService
from app.modules.territory.application.routes import RouteService
from app.modules.territory.application.zones import ZoneService
from app.modules.territory.domain.entities import (
    FacetCount,
    Leg,
    LoadCounts,
    MapPoint,
    OfficialZoneData,
    Point,
    Prospect,
    ProspectChange,
    ProspectData,
    ProspectFacets,
    ProspectFilters,
    ProspectLoad,
    ProspectPage,
    RoadPath,
    Route,
    RouteFields,
    RoutePage,
    RouteStop,
    RouteSummary,
    StopData,
    Zone,
)
from app.modules.territory.domain.ports import RoutingUnavailable
from app.modules.territory.domain.vocabulary import MANUAL, PENDING
from app.modules.territory.infrastructure.prospect_file import SpreadsheetProspectReader

MANAGER = "comercial@jdh.com.co"
HEADER = [
    "ID",
    "Establecimiento",
    "Categoría",
    "Cliente antiguo",
    "Última compra",
    "Dirección",
    "Sector",
    "Ubicación",
    "Teléfono / WhatsApp",
    "Correo",
    "Página web",
    "Mapa",
    "Observación",
]


def person(email: str = "ana@jdh.com.co", admin: bool = False) -> Principal:
    return Principal(user_id=uuid4(), email=email, display_name=email, is_platform_admin=admin)


def maps_link(latitude: float, longitude: float) -> str:
    return f"https://www.google.com/maps?q={latitude},{longitude}"


def workbook(sheets: dict[str, list[dict[str, Any]]], header: list[str] = HEADER) -> bytes:
    """A prospects workbook: ``{sheet: [{column: value, "link": map url}]}``."""

    book = openpyxl.Workbook()
    book.remove(book.active)
    summary = book.create_sheet("Consolidado")
    summary.append(["Zona", "Registros"])
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        sheet.append(header)
        for row in rows:
            sheet.append([row.get(column) for column in header])
            link = row.get("link")
            if link and "Mapa" in header:
                cell = sheet.cell(row=sheet.max_row, column=header.index("Mapa") + 1)
                cell.value = "Ver mapa"
                cell.hyperlink = link
    discarded = book.create_sheet("Descartados")
    discarded.append(["Zona", "ID", "Establecimiento", "Motivo"])
    discarded.append(["Belén", "BEL-999", "Ya es cliente", "Cliente activo"])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def row(external_id: str, name: str, latitude: float | None = 6.25, longitude=-75.6, **values):
    data = {"ID": external_id, "Establecimiento": name, "Categoría": "Restaurante / comida"}
    data.update(values)
    if latitude is not None:
        data["link"] = maps_link(latitude, longitude)
    return data


class FakeManagers:
    def __init__(self, *emails: str, readers: Sequence[str] = ()) -> None:
        self.emails = set(emails)
        self.readers = set(readers)

    async def is_manager(self, principal: Principal) -> bool:
        return principal.is_platform_admin or principal.email in self.emails

    async def can_read(self, principal: Principal) -> bool:
        return await self.is_manager(principal) or principal.email in self.readers

    async def require(self, principal: Principal) -> None:
        if not await self.is_manager(principal):
            raise ForbiddenError("No tienes permiso para gestionar mapa-comercial")


class FakeProspects:
    def __init__(self) -> None:
        self.items: dict[UUID, Prospect] = {}
        self.loads_made: list[ProspectLoad] = []
        # Official municipality of a point, by latitude rounded to 2 decimals.
        self.municipality_at: dict[float, str] = {}

    async def municipalities(self, points: Sequence[Point]) -> list[str | None]:
        return [self.municipality_at.get(round(point.latitude, 2)) for point in points]

    def by_external(self, external_id: str) -> Prospect | None:
        return next((p for p in self.items.values() if p.data.external_id == external_id), None)

    def add(self, data: ProspectData, **values: Any) -> Prospect:
        now = datetime.now(UTC)
        prospect = Prospect(
            id=uuid4(),
            data=data,
            commercial_zone=None,
            visit_status=values.get("visit_status", PENDING),
            visit_note=None,
            visited_at=None,
            load_id=None,
            created_at=now,
            updated_at=now,
        )
        self.items[prospect.id] = prospect
        return prospect

    async def stored(self, external_ids: Sequence[str]) -> dict[str, ProspectData]:
        wanted = set(external_ids)
        return {
            p.data.external_id: p.data for p in self.items.values() if p.data.external_id in wanted
        }

    async def apply_load(
        self, file_name, rows, counts: LoadCounts, loaded_by_id, loaded_by
    ) -> UUID:
        load_id = uuid4()
        for data in rows:
            current = self.by_external(data.external_id)
            if current is None:
                self.add(data)
                continue
            if current.data.location_quality == MANUAL:
                data = replace(data, location=current.data.location, location_quality=MANUAL)
            self.items[current.id] = replace(current, data=data, load_id=load_id)
        self.loads_made.append(
            ProspectLoad(
                id=load_id,
                file_name=file_name,
                rows_read=len(rows),
                created=counts.created,
                updated=counts.updated,
                unchanged=counts.unchanged,
                loaded_by=loaded_by,
                loaded_at=datetime.now(UTC),
            )
        )
        return load_id

    def _matches(self, prospect: Prospect, filters: ProspectFilters) -> bool:
        data = prospect.data
        if filters.source_zone and data.source_zone != filters.source_zone:
            return False
        if filters.located is not None and (data.location is not None) != filters.located:
            return False
        if filters.former is not None and data.is_former_customer != filters.former:
            return False
        return not (filters.q and filters.q.lower() not in data.name.lower())

    async def map_points(self, filters: ProspectFilters) -> list[MapPoint]:
        return [
            MapPoint(
                id=p.id,
                external_id=p.data.external_id,
                name=p.data.name,
                category=p.data.category,
                source_zone=p.data.source_zone,
                sector=p.data.sector,
                is_former_customer=p.data.is_former_customer,
                location_quality=p.data.location_quality,
                commercial_zone_id=None,
                visit_status=p.visit_status,
                location=p.data.location,
            )
            for p in self.items.values()
            if p.data.location is not None and self._matches(p, filters)
        ]

    async def page(self, filters: ProspectFilters, page: int, size: int) -> ProspectPage:
        found = sorted(
            (p for p in self.items.values() if self._matches(p, filters)),
            key=lambda p: p.data.name,
        )
        return ProspectPage(
            items=found[(page - 1) * size : page * size], total=len(found), page=page, size=size
        )

    async def facets(self) -> ProspectFacets:
        zones: dict[str, int] = {}
        for p in self.items.values():
            zones[p.data.source_zone] = zones.get(p.data.source_zone, 0) + 1
        located = sum(1 for p in self.items.values() if p.data.location)
        return ProspectFacets(
            source_zones=[FacetCount(value=k, count=v) for k, v in sorted(zones.items())],
            categories=[],
            total=len(self.items),
            located=located,
            unlocated=len(self.items) - located,
        )

    async def get(self, prospect_id: UUID) -> Prospect | None:
        return self.items.get(prospect_id)

    async def get_many(self, prospect_ids: Sequence[UUID]) -> list[Prospect]:
        return [self.items[i] for i in prospect_ids if i in self.items]

    async def update(self, prospect_id: UUID, change: ProspectChange) -> Prospect | None:
        prospect = self.items.get(prospect_id)
        if prospect is None:
            return None
        data = prospect.data
        if change.location is not None:
            data = replace(data, location=change.location, location_quality=MANUAL)
        prospect = replace(prospect, data=data)
        if change.visit_status is not None:
            prospect = replace(prospect, visit_status=change.visit_status)
        if change.set_note:
            prospect = replace(prospect, visit_note=change.visit_note)
        self.items[prospect_id] = prospect
        return prospect

    async def loads(self, limit: int) -> list[ProspectLoad]:
        return list(reversed(self.loads_made))[:limit]


class FakeZones:
    def __init__(self) -> None:
        self.items: dict[UUID, Zone] = {}

    def add(self, name: str, layer: str = "commercial", **values: Any) -> Zone:
        zone = Zone(
            id=uuid4(),
            layer=layer,
            level=values.get("level", "custom" if layer == "commercial" else "commune"),
            name=name,
            code=values.get("code"),
            parent_id=None,
            color=values.get("color", "blue" if layer == "commercial" else None),
            source=None,
            prospect_count=0,
            geometry=values.get("geometry", {"type": "MultiPolygon", "coordinates": []}),
        )
        self.items[zone.id] = zone
        return zone

    async def list(self, layer, level, simplify):
        return [z for z in self.items.values() if z.layer == layer and level in (None, z.level)]

    async def get(self, zone_id):
        return self.items.get(zone_id)

    async def name_taken(self, name, exclude_id=None):
        return any(
            z.layer == "commercial" and z.name.lower() == name.lower() and z.id != exclude_id
            for z in self.items.values()
        )

    async def create(self, name, color, geometry, official_ids, created_by_id):
        return self.add(name, color=color, geometry=geometry or {"type": "MultiPolygon"})

    async def update(self, zone_id, name, color, geometry):
        zone = self.items.get(zone_id)
        if zone is None:
            return None
        zone = replace(
            zone,
            name=name or zone.name,
            color=color or zone.color,
            geometry=geometry or zone.geometry,
        )
        self.items[zone_id] = zone
        return zone

    async def delete(self, zone_id):
        return self.items.pop(zone_id, None) is not None

    async def official_count(self, zone_ids):
        return sum(1 for i in zone_ids if i in self.items and self.items[i].layer == "official")

    async def upsert_official(self, items: Sequence[OfficialZoneData], source):
        for item in items:
            self.add(item.name, layer="official", level=item.level, code=item.code)
        return len(items), 0


class FakeRoutes:
    def __init__(self) -> None:
        self.items: dict[UUID, Route] = {}

    async def page(self, assignee_id, page, size) -> RoutePage:
        routes = [r for r in self.items.values() if assignee_id in (None, r.assignee_id)]
        return RoutePage(
            items=[
                RouteSummary(
                    id=r.id,
                    name=r.name,
                    planned_for=r.planned_for,
                    assignee_id=r.assignee_id,
                    stop_count=len(r.stops),
                    visited_count=sum(1 for s in r.stops if s.data.visit_status != PENDING),
                    geometry_status=r.path.geometry_status,
                    distance_m=r.path.distance_m,
                    duration_s=r.path.duration_s,
                    created_by_id=r.created_by_id,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                )
                for r in routes
            ],
            total=len(routes),
            page=page,
            size=size,
        )

    async def get(self, route_id):
        return self.items.get(route_id)

    @staticmethod
    def _stops(stops: Sequence[StopData]) -> list[RouteStop]:
        return [RouteStop(id=uuid4(), position=i, data=s) for i, s in enumerate(stops, 1)]

    async def create(self, fields: RouteFields, stops, path, created_by_id) -> Route:
        now = datetime.now(UTC)
        route = Route(
            id=uuid4(),
            name=fields.name or "",
            planned_for=fields.planned_for,
            assignee_id=fields.assignee_id,
            origin=fields.origin,
            origin_label=fields.origin_label,
            path=path,
            stops=self._stops(stops),
            created_by_id=created_by_id,
            created_at=now,
            updated_at=now,
        )
        self.items[route.id] = route
        return route

    async def update(self, route_id, fields: RouteFields, stops, path):
        route = self.items.get(route_id)
        if route is None:
            return None
        changes: dict[str, Any] = {}
        if fields.name:
            changes["name"] = fields.name
        for key in ("planned_for", "assignee_id", "origin", "origin_label"):
            if key in fields.set_fields:
                changes[key] = getattr(fields, key)
        if stops is not None:
            changes["stops"] = self._stops(stops)
        if path is not None:
            changes["path"] = path
        route = replace(route, **changes)
        self.items[route_id] = route
        return route

    async def update_stop(self, route_id, stop_id, visit_status, note, set_note):
        route = self.items.get(route_id)
        if route is None:
            return None
        for index, stop in enumerate(route.stops):
            if stop.id == stop_id:
                data = stop.data
                if visit_status is not None:
                    data = replace(data, visit_status=visit_status)
                if set_note:
                    data = replace(data, visit_note=note)
                route.stops[index] = replace(stop, data=data)
                return route.stops[index]
        return None

    async def delete(self, route_id):
        return self.items.pop(route_id, None) is not None


class FakeRouting:
    def __init__(self, enabled: bool = True, fail: str | None = None) -> None:
        self.enabled = enabled
        self.fail = fail
        self.calls: list[str] = []

    async def close(self) -> None:
        return None

    async def optimize(self, start: Point, stops: Sequence[Point]) -> list[int]:
        self.calls.append("optimize")
        if self.fail:
            raise RoutingUnavailable(self.fail)
        return list(reversed(range(len(stops))))

    async def directions(self, points: Sequence[Point]) -> RoadPath:
        self.calls.append("directions")
        if self.fail:
            raise RoutingUnavailable(self.fail)
        legs = [
            Leg(distance_m=1000.0 * (i + 1), duration_s=60.0 * (i + 1))
            for i in range(len(points) - 1)
        ]
        return RoadPath(
            coordinates=[[p.longitude, p.latitude] for p in points],
            distance_m=sum(leg.distance_m for leg in legs),
            duration_s=sum(leg.duration_s for leg in legs),
            legs=legs,
        )


class FakeUsers:
    def __init__(self) -> None:
        self.people: dict[str, DirectoryUser] = {}

    def add(self, email: str) -> DirectoryUser:
        user = DirectoryUser(id=uuid4(), email=email, display_name=email.split("@")[0])
        self.people[email] = user
        return user

    async def require_user(self, email: str) -> DirectoryUser:
        if email not in self.people:
            raise NotFoundError(f"No hay un usuario con el correo {email}")
        return self.people[email]

    async def get_users(self, user_ids) -> dict[UUID, DirectoryUser]:
        wanted = set(user_ids)
        return {u.id: u for u in self.people.values() if u.id in wanted}


@pytest.fixture
def prospects() -> FakeProspects:
    return FakeProspects()


@pytest.fixture
def zones() -> FakeZones:
    return FakeZones()


@pytest.fixture
def routes() -> FakeRoutes:
    return FakeRoutes()


@pytest.fixture
def routing() -> FakeRouting:
    return FakeRouting()


@pytest.fixture
def users() -> FakeUsers:
    return FakeUsers()


@pytest.fixture
def managers() -> FakeManagers:
    return FakeManagers(MANAGER, readers=["lector@jdh.com.co"])


@pytest.fixture
def prospect_service(prospects, managers) -> ProspectService:
    return ProspectService(prospects, SpreadsheetProspectReader(), managers, 5 * 1024 * 1024)


@pytest.fixture
def zone_service(zones, managers) -> ZoneService:
    return ZoneService(zones, managers)


@pytest.fixture
def route_service(routes, prospects, routing, users, managers) -> RouteService:
    return RouteService(routes, prospects, routing, users, managers, max_stops=48)
