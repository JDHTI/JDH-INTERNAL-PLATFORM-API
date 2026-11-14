"""Values of the commercial map: prospects, their loads, zones and visit routes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID

from app.modules.territory.domain.contacts import Phone


@dataclass(frozen=True)
class Point:
    """A WGS84 point."""

    latitude: float
    longitude: float


# --- Prospects -------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProspectData:
    """What a load brings of one prospect (one row of the monthly file)."""

    external_id: str
    source_zone: str
    name: str
    category: str | None = None
    is_former_customer: bool = False
    last_purchase_month: date | None = None
    address: str | None = None
    sector: str | None = None
    area_status: str | None = None
    phone_raw: str | None = None
    email_raw: str | None = None
    phones: tuple[Phone, ...] = ()
    emails: tuple[str, ...] = ()
    website: str | None = None
    observation: str | None = None
    location: Point | None = None
    location_quality: str = "missing"

    def comparable(self) -> tuple[Any, ...]:
        """The fields a new load compares to tell an update from no change."""

        point = (
            (round(self.location.latitude, 7), round(self.location.longitude, 7))
            if self.location
            else None
        )
        return (
            self.source_zone,
            self.name,
            self.category,
            self.is_former_customer,
            self.last_purchase_month,
            self.address,
            self.sector,
            self.area_status,
            self.phone_raw,
            self.email_raw,
            self.website,
            self.observation,
            point,
            self.location_quality,
        )


@dataclass(frozen=True)
class ZoneRef:
    """The commercial zone a prospect falls in."""

    id: UUID
    name: str
    color: str | None


@dataclass(frozen=True)
class Prospect:
    """A stored prospect."""

    id: UUID
    data: ProspectData
    commercial_zone: ZoneRef | None
    visit_status: str
    visit_note: str | None
    visited_at: datetime | None
    load_id: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class MapPoint:
    """The light version of a prospect the map paints."""

    id: UUID
    external_id: str
    name: str
    category: str | None
    source_zone: str
    sector: str | None
    is_former_customer: bool
    location_quality: str
    commercial_zone_id: UUID | None
    visit_status: str
    location: Point


@dataclass(frozen=True)
class ProspectFilters:
    """Filters of the map and the list; ``None`` means any."""

    source_zone: str | None = None
    category: str | None = None
    zone_id: UUID | None = None
    former: bool | None = None
    visit_status: str | None = None
    q: str | None = None
    located: bool | None = None


@dataclass(frozen=True)
class ProspectPage:
    items: list[Prospect]
    total: int
    page: int
    size: int


@dataclass(frozen=True)
class FacetCount:
    value: str
    count: int


@dataclass(frozen=True)
class ProspectFacets:
    """Values the filters offer, with how many prospects have each."""

    source_zones: list[FacetCount]
    categories: list[FacetCount]
    total: int
    located: int
    unlocated: int


@dataclass(frozen=True)
class ProspectChange:
    """A change made by hand: a moved point or the result of a visit."""

    location: Point | None = None
    visit_status: str | None = None
    visit_note: str | None = None
    # Whether ``visit_note`` was sent (``None`` then clears it).
    set_note: bool = False


# --- Loads -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class RowIssue:
    """A problem of one row of the file."""

    sheet: str
    row_number: int
    field: str | None
    message: str


@dataclass(frozen=True)
class SheetSummary:
    name: str
    rows: int
    with_location: int
    without_location: int


@dataclass(frozen=True)
class ParsedFile:
    """What the reader got from a file."""

    rows: list[ProspectData]
    sheets: list[SheetSummary]
    errors: list[RowIssue]
    # Non-empty rows of the prospect sheets, with or without problems.
    rows_read: int = 0


@dataclass(frozen=True)
class LoadCounts:
    created: int
    updated: int
    unchanged: int


@dataclass(frozen=True)
class ImportReport:
    """Result of a load, simulated or applied."""

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
    sheets: list[SheetSummary]
    errors: list[RowIssue]
    errors_truncated: bool


@dataclass(frozen=True)
class ProspectLoad:
    id: UUID
    file_name: str
    rows_read: int
    created: int
    updated: int
    unchanged: int
    loaded_by: str | None
    loaded_at: datetime


# --- Zones -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Zone:
    """A polygon of the official layer (reference) or the commercial one (JDH's)."""

    id: UUID
    layer: str
    level: str
    name: str
    code: str | None
    parent_id: UUID | None
    color: str | None
    source: str | None
    prospect_count: int
    # GeoJSON ``Polygon`` or ``MultiPolygon``.
    geometry: dict[str, Any]


@dataclass(frozen=True)
class OfficialZoneData:
    """One polygon of an official layer to load."""

    level: str
    name: str
    code: str
    # GeoJSON ``Polygon`` or ``MultiPolygon``.
    geometry: dict[str, Any]
    parent_code: str | None = None


# --- Routes ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Assignee:
    id: UUID
    email: str | None
    display_name: str | None


@dataclass(frozen=True)
class StopData:
    """A stop to store: a copy of the prospect, so the route outlives new loads."""

    prospect_id: UUID | None
    name: str
    address: str | None
    location: Point
    visit_status: str = "pending"
    visit_note: str | None = None
    visited_at: datetime | None = None
    leg_distance_m: int | None = None
    leg_duration_s: int | None = None


@dataclass(frozen=True)
class RouteStop:
    id: UUID
    position: int
    data: StopData


@dataclass(frozen=True)
class RoutePath:
    """How the stops are joined: by road, in straight lines or not yet."""

    geometry_status: str
    # GeoJSON ``LineString`` or ``None``.
    geometry: dict[str, Any] | None = None
    distance_m: int | None = None
    duration_s: int | None = None
    # Why the route is not by road, when it is not.
    routing_note: str | None = None


@dataclass(frozen=True)
class Route:
    id: UUID
    name: str
    planned_for: date | None
    assignee_id: UUID | None
    origin: Point | None
    origin_label: str | None
    path: RoutePath
    stops: list[RouteStop]
    created_by_id: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class RouteSummary:
    id: UUID
    name: str
    planned_for: date | None
    assignee_id: UUID | None
    stop_count: int
    visited_count: int
    geometry_status: str
    distance_m: int | None
    duration_s: int | None
    created_by_id: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class RoutePage:
    items: list[RouteSummary]
    total: int
    page: int
    size: int


@dataclass(frozen=True)
class RouteFields:
    """The editable fields of a route; ``None`` in an update keeps the stored value."""

    name: str | None = None
    planned_for: date | None = None
    assignee_id: UUID | None = None
    origin: Point | None = None
    origin_label: str | None = None
    # Which of the nullable fields an update sets (to ``None`` too).
    set_fields: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Leg:
    distance_m: float | None
    duration_s: float | None


@dataclass(frozen=True)
class RoadPath:
    """What the routing service answers for an ordered list of points."""

    # ``[lon, lat]`` pairs of the line.
    coordinates: list[list[float]]
    distance_m: float
    duration_s: float
    legs: list[Leg]


__all__ = [
    "Assignee",
    "FacetCount",
    "ImportReport",
    "Leg",
    "LoadCounts",
    "MapPoint",
    "OfficialZoneData",
    "ParsedFile",
    "Point",
    "Prospect",
    "ProspectChange",
    "ProspectData",
    "ProspectFacets",
    "ProspectFilters",
    "ProspectLoad",
    "ProspectPage",
    "RoadPath",
    "Route",
    "RouteFields",
    "RoutePage",
    "RoutePath",
    "RouteStop",
    "RouteSummary",
    "RowIssue",
    "SheetSummary",
    "StopData",
    "Zone",
    "ZoneRef",
]
