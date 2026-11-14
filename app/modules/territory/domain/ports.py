"""Ports of the commercial map: storage, the file reader, routing and permissions."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Protocol
from uuid import UUID

from app.modules.identity.domain.principal import DirectoryUser, Principal
from app.modules.territory.domain.entities import (
    LoadCounts,
    MapPoint,
    OfficialZoneData,
    ParsedFile,
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
    RoutePath,
    RouteStop,
    StopData,
    Zone,
)


class RoutingUnavailable(Exception):
    """The routing service did not answer (down, quota spent, no key)."""


class ManagerPolicy(Protocol):
    async def is_manager(self, principal: Principal) -> bool: ...

    async def can_read(self, principal: Principal) -> bool: ...

    async def require(self, principal: Principal) -> None: ...


class UserLookup(Protocol):
    async def require_user(self, email: str) -> DirectoryUser: ...

    async def get_users(self, user_ids: Iterable[UUID]) -> dict[UUID, DirectoryUser]: ...


class ProspectFileReader(Protocol):
    def read(self, content: bytes) -> ParsedFile:
        """Read the prospects of a monthly file.

        Args:
            content (bytes): The ``.xlsx`` file.

        Returns:
            ParsedFile: Rows, per-sheet counts and row problems.

        Raises:
            InvalidInputError: When the file cannot be read or has no prospect sheet.
        """
        ...


class ProspectRepository(Protocol):
    async def stored(self, external_ids: Sequence[str]) -> dict[str, ProspectData]:
        """The stored data of the prospects with these file ids.

        Args:
            external_ids (Sequence[str]): ``ID`` column values.

        Returns:
            dict[str, ProspectData]: By file id; missing ids are new.
        """
        ...

    async def apply_load(
        self,
        file_name: str,
        rows: Sequence[ProspectData],
        counts: LoadCounts,
        loaded_by_id: UUID | None,
        loaded_by: str | None,
    ) -> UUID:
        """Store a load in one transaction and place the prospects in their zones.

        A point moved by hand and the visit result are kept.

        Args:
            file_name (str): Uploaded file name.
            rows (Sequence[ProspectData]): Every row of the file.
            counts (LoadCounts): Created, updated and unchanged, for the trace.
            loaded_by_id (UUID | None): Who loaded it.
            loaded_by (str | None): Their e-mail, for the trace.

        Returns:
            UUID: The load id.
        """
        ...

    async def map_points(self, filters: ProspectFilters) -> list[MapPoint]:
        """Every located prospect that matches the filters.

        Args:
            filters (ProspectFilters): Filters of the map.

        Returns:
            list[MapPoint]: Light rows for the map.
        """
        ...

    async def page(self, filters: ProspectFilters, page: int, size: int) -> ProspectPage:
        """A page of prospects ordered by name.

        Args:
            filters (ProspectFilters): Filters of the list.
            page (int): 1-based page.
            size (int): Page size.

        Returns:
            ProspectPage: The page and the total.
        """
        ...

    async def facets(self) -> ProspectFacets:
        """Zones and categories with their counts, and how many are located.

        Returns:
            ProspectFacets: Values for the filters.
        """
        ...

    async def get(self, prospect_id: UUID) -> Prospect | None:
        """One prospect.

        Args:
            prospect_id (UUID): Its id.

        Returns:
            Prospect | None: The prospect, or ``None``.
        """
        ...

    async def get_many(self, prospect_ids: Sequence[UUID]) -> list[Prospect]:
        """Several prospects, in no particular order.

        Args:
            prospect_ids (Sequence[UUID]): Their ids.

        Returns:
            list[Prospect]: The ones that exist.
        """
        ...

    async def update(self, prospect_id: UUID, change: ProspectChange) -> Prospect | None:
        """Apply a change made by hand; a moved point is ``manual`` and changes zone.

        Args:
            prospect_id (UUID): The prospect.
            change (ProspectChange): What changes.

        Returns:
            Prospect | None: The prospect after the change, or ``None`` when missing.
        """
        ...

    async def municipalities(self, points: Sequence[Point]) -> list[str | None]:
        """The official municipality each point falls in.

        Args:
            points (Sequence[Point]): Points to place.

        Returns:
            list[str | None]: Names in the same order; ``None`` outside every loaded one.
        """
        ...

    async def loads(self, limit: int) -> list[ProspectLoad]:
        """The latest loads, newest first.

        Args:
            limit (int): How many.

        Returns:
            list[ProspectLoad]: The loads.
        """
        ...


class ZoneRepository(Protocol):
    async def list(self, layer: str, level: str | None, simplify: bool) -> list[Zone]:
        """Zones of a layer, smallest last so they paint on top.

        Args:
            layer (str): ``official`` or ``commercial``.
            level (str | None): Only this level.
            simplify (bool): Lighter geometry, for painting.

        Returns:
            list[Zone]: The zones with their prospect count (commercial only).
        """
        ...

    async def get(self, zone_id: UUID) -> Zone | None:
        """One zone with its full geometry.

        Args:
            zone_id (UUID): Its id.

        Returns:
            Zone | None: The zone, or ``None``.
        """
        ...

    async def name_taken(self, name: str, exclude_id: UUID | None = None) -> bool:
        """Whether a commercial zone already has this name (any case).

        Args:
            name (str): The name.
            exclude_id (UUID | None): The zone being renamed.

        Returns:
            bool: ``True`` when taken.
        """
        ...

    async def create(
        self,
        name: str,
        color: str,
        geometry: dict[str, Any] | None,
        official_ids: Sequence[UUID],
        created_by_id: UUID | None,
    ) -> Zone:
        """Create a commercial zone from a drawn shape or the union of official zones.

        The geometry is repaired; prospects are placed again.

        Args:
            name (str): Its name.
            color (str): Palette key.
            geometry (dict[str, Any] | None): GeoJSON polygon, when drawn.
            official_ids (Sequence[UUID]): Official zones to join, when adopted.
            created_by_id (UUID | None): Who created it.

        Returns:
            Zone: The new zone.

        Raises:
            InvalidInputError: When the shape is empty once repaired.
        """
        ...

    async def update(
        self,
        zone_id: UUID,
        name: str | None,
        color: str | None,
        geometry: dict[str, Any] | None,
    ) -> Zone | None:
        """Change a commercial zone; a new shape places the prospects again.

        Args:
            zone_id (UUID): The zone.
            name (str | None): New name, or keep.
            color (str | None): New color, or keep.
            geometry (dict[str, Any] | None): New shape, or keep.

        Returns:
            Zone | None: The zone, or ``None`` when missing.

        Raises:
            InvalidInputError: When the shape is empty once repaired.
        """
        ...

    async def delete(self, zone_id: UUID) -> bool:
        """Delete a commercial zone; its prospects are placed again.

        Args:
            zone_id (UUID): The zone.

        Returns:
            bool: ``True`` when it existed.
        """
        ...

    async def official_count(self, zone_ids: Sequence[UUID]) -> int:
        """How many of these ids are official zones.

        Args:
            zone_ids (Sequence[UUID]): Ids to check.

        Returns:
            int: The count.
        """
        ...

    async def upsert_official(
        self, items: Sequence[OfficialZoneData], source: str | None
    ) -> tuple[int, int]:
        """Create or replace official zones by level and code.

        Args:
            items (Sequence[OfficialZoneData]): The polygons.
            source (str | None): Where they come from.

        Returns:
            tuple[int, int]: Created and replaced.
        """
        ...


class RouteRepository(Protocol):
    async def page(self, assignee_id: UUID | None, page: int, size: int) -> RoutePage:
        """Routes, the next planned first.

        Args:
            assignee_id (UUID | None): Only this person's routes.
            page (int): 1-based page.
            size (int): Page size.

        Returns:
            RoutePage: The page and the total.
        """
        ...

    async def get(self, route_id: UUID) -> Route | None:
        """One route with its stops in order.

        Args:
            route_id (UUID): Its id.

        Returns:
            Route | None: The route, or ``None``.
        """
        ...

    async def create(
        self,
        fields: RouteFields,
        stops: Sequence[StopData],
        path: RoutePath,
        created_by_id: UUID | None,
    ) -> Route:
        """Store a new route.

        Args:
            fields (RouteFields): Name, date, assignee and origin.
            stops (Sequence[StopData]): Stops in visiting order.
            path (RoutePath): How they are joined.
            created_by_id (UUID | None): Who created it.

        Returns:
            Route: The stored route.
        """
        ...

    async def update(
        self,
        route_id: UUID,
        fields: RouteFields,
        stops: Sequence[StopData] | None,
        path: RoutePath | None,
    ) -> Route | None:
        """Change a route; new stops replace the old ones.

        Args:
            route_id (UUID): The route.
            fields (RouteFields): What changes (``set_fields`` says which).
            stops (Sequence[StopData] | None): New stops in order, or keep.
            path (RoutePath | None): New path, or keep.

        Returns:
            Route | None: The route, or ``None`` when missing.
        """
        ...

    async def update_stop(
        self,
        route_id: UUID,
        stop_id: UUID,
        visit_status: str | None,
        note: str | None,
        set_note: bool,
    ) -> RouteStop | None:
        """Record the result of a visit on the stop and on its prospect.

        Args:
            route_id (UUID): The route.
            stop_id (UUID): The stop.
            visit_status (str | None): New status, or keep.
            note (str | None): New note.
            set_note (bool): Whether ``note`` is set (``None`` clears it).

        Returns:
            RouteStop | None: The stop, or ``None`` when missing.
        """
        ...

    async def delete(self, route_id: UUID) -> bool:
        """Delete a route with its stops.

        Args:
            route_id (UUID): The route.

        Returns:
            bool: ``True`` when it existed.
        """
        ...


class RoutingProvider(Protocol):
    @property
    def enabled(self) -> bool:
        """Whether a routing service is configured."""
        ...

    async def optimize(self, start: Point, stops: Sequence[Point]) -> list[int]:
        """The visiting order that takes the least time.

        Args:
            start (Point): Where the vehicle starts.
            stops (Sequence[Point]): Points to visit.

        Returns:
            list[int]: Indexes of ``stops`` in visiting order.

        Raises:
            RoutingUnavailable: When the service does not answer.
        """
        ...

    async def directions(self, points: Sequence[Point]) -> RoadPath:
        """The road path through the points, in their order.

        Args:
            points (Sequence[Point]): Two or more points.

        Returns:
            RoadPath: The line, its distance and duration, and each leg.

        Raises:
            RoutingUnavailable: When the service does not answer.
        """
        ...


__all__ = [
    "ManagerPolicy",
    "ProspectFileReader",
    "ProspectRepository",
    "RouteRepository",
    "RoutingProvider",
    "RoutingUnavailable",
    "UserLookup",
    "ZoneRepository",
]
