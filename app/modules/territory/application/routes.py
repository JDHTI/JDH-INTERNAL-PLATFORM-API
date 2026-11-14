"""Visit routes: stops chosen on the map, ordered and joined by road (or straight lines).

The routing service is called only when a route is created or recomputed; the line is stored,
so opening a route costs nothing. When the service is off, out of quota or down, the route is
still saved: a greedy order and straight lines, marked ``straight`` with the reason.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import date
from uuid import UUID

from app.core.errors import ForbiddenError, InvalidInputError, NotFoundError
from app.modules.identity.domain.principal import DirectoryUser, Principal
from app.modules.territory.domain.entities import (
    Point,
    Route,
    RouteFields,
    RoutePage,
    RoutePath,
    RouteStop,
    StopData,
)
from app.modules.territory.domain.ports import (
    ManagerPolicy,
    ProspectRepository,
    RouteRepository,
    RoutingProvider,
    RoutingUnavailable,
    UserLookup,
)
from app.modules.territory.domain.routing import line, nearest_neighbor_order, straight_distance
from app.modules.territory.domain.vocabulary import (
    NOT_COMPUTED,
    ROAD,
    STRAIGHT,
    VISIT_STATUSES,
    in_colombia,
)

MIN_STOPS = 2
_NOT_CONFIGURED = "El ruteo por calles no está configurado; la ruta queda en línea recta"


def _name(name: str) -> str:
    cleaned = " ".join((name or "").split())
    if not cleaned or len(cleaned) > 160:
        raise InvalidInputError("El nombre de la ruta debe tener entre 1 y 160 caracteres")
    return cleaned


def _origin(origin: Point | None) -> Point | None:
    if origin is not None and not in_colombia(origin.latitude, origin.longitude):
        raise InvalidInputError("El origen está fuera de Colombia")
    return origin


class RouteService:
    """``read`` sees the routes; ``manage`` plans them; the assignee records the visits."""

    def __init__(
        self,
        routes: RouteRepository,
        prospects: ProspectRepository,
        routing: RoutingProvider,
        users: UserLookup,
        managers: ManagerPolicy,
        max_stops: int,
    ) -> None:
        """Create the service.

        Args:
            routes (RouteRepository): Routes storage.
            prospects (ProspectRepository): Where the stops come from.
            routing (RoutingProvider): Road routing and stop ordering.
            users (UserLookup): Resolves the assignee.
            managers (ManagerPolicy): Who manages ``mapa-comercial``.
            max_stops (int): Most stops per route (the routing plan's limit).
        """

        self.routes = routes
        self.prospects = prospects
        self.routing = routing
        self.users = users
        self.managers = managers
        self.max_stops = max_stops

    async def people(self, user_ids: Iterable[UUID | None]) -> dict[UUID, DirectoryUser]:
        """The users behind some ids (assignees, creators).

        Args:
            user_ids (Iterable[UUID | None]): Ids; ``None`` is skipped.

        Returns:
            dict[UUID, DirectoryUser]: Found users by id.
        """

        ids = {user_id for user_id in user_ids if user_id is not None}
        return await self.users.get_users(ids) if ids else {}

    async def page(self, principal: Principal, mine: bool, page: int, size: int) -> RoutePage:
        """Routes, all of them or the caller's.

        Args:
            principal (Principal): The caller.
            mine (bool): Only the routes assigned to the caller.
            page (int): 1-based page.
            size (int): Page size.

        Returns:
            RoutePage: The page.
        """

        return await self.routes.page(principal.user_id if mine else None, page, size)

    async def get(self, route_id: UUID) -> Route:
        """One route with its stops and line.

        Args:
            route_id (UUID): Its id.

        Returns:
            Route: The route.

        Raises:
            NotFoundError: When it does not exist.
        """

        route = await self.routes.get(route_id)
        if route is None:
            raise NotFoundError("Ruta no encontrada")
        return route

    async def _stops(self, prospect_ids: Sequence[UUID]) -> list[StopData]:
        ids = list(prospect_ids)
        if len(set(ids)) != len(ids):
            raise InvalidInputError("Un prospecto aparece dos veces en la ruta")
        if not MIN_STOPS <= len(ids) <= self.max_stops:
            raise InvalidInputError(f"Una ruta lleva entre {MIN_STOPS} y {self.max_stops} paradas")
        found = {item.id: item for item in await self.prospects.get_many(ids)}
        missing = [str(item) for item in ids if item not in found]
        if missing:
            raise InvalidInputError(f"Prospectos no encontrados: {', '.join(missing)}")
        unlocated = [found[item].data.name for item in ids if found[item].data.location is None]
        if unlocated:
            raise InvalidInputError(f"Sin ubicación en el mapa: {', '.join(unlocated)}")
        return [
            StopData(
                prospect_id=item,
                name=found[item].data.name,
                address=found[item].data.address,
                location=found[item].data.location,  # type: ignore[arg-type]
            )
            for item in ids
        ]

    async def _assignee(self, email: str | None) -> UUID | None:
        if not email or not email.strip():
            return None
        return (await self.users.require_user(email.strip())).id

    async def _plan(
        self, origin: Point | None, stops: Sequence[StopData], optimize: bool
    ) -> tuple[list[StopData], RoutePath]:
        """Order the stops (when asked) and join them by road, or in straight lines."""

        points = [stop.location for stop in stops]
        start = origin or points[0]
        note: str | None = None if self.routing.enabled else _NOT_CONFIGURED
        order = list(range(len(stops)))
        if optimize:
            if note is None:
                try:
                    order = await self.routing.optimize(start, points)
                except RoutingUnavailable as exc:
                    note = str(exc)
            if note is not None:
                order = nearest_neighbor_order(start, points)
            if origin is None and order[0] != 0:
                # Without an origin the route starts at its first stop.
                order = [0, *[index for index in order if index != 0]]
        ordered = [stops[index] for index in order]
        through = ([origin] if origin else []) + [stop.location for stop in ordered]
        # Leg i of the line arrives at ``through[i + 1]``: with an origin, at stop i.
        first_leg_stop = 0 if origin else 1
        if note is None:
            try:
                road = await self.routing.directions(through)
            except RoutingUnavailable as exc:
                note = str(exc)
            else:
                with_legs = list(ordered)
                for offset, leg in enumerate(road.legs):
                    index = first_leg_stop + offset
                    if index < len(with_legs):
                        with_legs[index] = replace(
                            with_legs[index],
                            leg_distance_m=round(leg.distance_m) if leg.distance_m else 0,
                            leg_duration_s=round(leg.duration_s) if leg.duration_s else 0,
                        )
                return with_legs, RoutePath(
                    geometry_status=ROAD,
                    geometry={"type": "LineString", "coordinates": road.coordinates},
                    distance_m=round(road.distance_m),
                    duration_s=round(road.duration_s),
                )
        legs = [round(straight_distance(a, b)) for a, b in zip(through, through[1:], strict=False)]
        with_legs = [replace(stop, leg_distance_m=None, leg_duration_s=None) for stop in ordered]
        for offset, distance in enumerate(legs):
            index = first_leg_stop + offset
            if index < len(with_legs):
                with_legs[index] = replace(with_legs[index], leg_distance_m=distance)
        return with_legs, RoutePath(
            geometry_status=STRAIGHT,
            geometry=line(through),
            distance_m=sum(legs),
            duration_s=None,
            routing_note=note,
        )

    async def create(
        self,
        principal: Principal,
        *,
        name: str,
        prospect_ids: Sequence[UUID],
        planned_for: date | None = None,
        assignee_email: str | None = None,
        origin: Point | None = None,
        origin_label: str | None = None,
        optimize: bool = True,
    ) -> Route:
        """Plan a route through some prospects and compute it (``manage``).

        Args:
            principal (Principal): The caller.
            name (str): Its name.
            prospect_ids (Sequence[UUID]): Stops, in the order to keep when not optimizing.
            planned_for (date | None): The day of the visit.
            assignee_email (str | None): Who visits.
            origin (Point | None): Where the visit starts; the first stop when missing.
            origin_label (str | None): Name of the origin (``Bodega``).
            optimize (bool): Reorder the stops for the shortest time.

        Returns:
            Route: The stored route.

        Raises:
            ForbiddenError: Without ``manage``.
            InvalidInputError: With a bad name, too few or many stops, or unlocated ones.
            NotFoundError: When the assignee is not a user.
        """

        await self.managers.require(principal)
        fields = RouteFields(
            name=_name(name),
            planned_for=planned_for,
            assignee_id=await self._assignee(assignee_email),
            origin=_origin(origin),
            origin_label=(origin_label or "").strip()[:160] or None,
        )
        stops, path = await self._plan(origin, await self._stops(prospect_ids), optimize)
        return await self.routes.create(fields, stops, path, principal.user_id)

    async def update(
        self,
        principal: Principal,
        route_id: UUID,
        *,
        name: str | None = None,
        planned_for: date | None = None,
        assignee_email: str | None = None,
        origin: Point | None = None,
        origin_label: str | None = None,
        prospect_ids: Sequence[UUID] | None = None,
        sent: frozenset[str] = frozenset(),
    ) -> Route:
        """Change a route (``manage``). New stops or origin leave it ``pending`` to recompute.

        Args:
            principal (Principal): The caller.
            route_id (UUID): The route.
            name (str | None): New name.
            planned_for (date | None): New day (``None`` clears it when sent).
            assignee_email (str | None): New assignee (``None`` clears it when sent).
            origin (Point | None): New origin (``None`` clears it when sent).
            origin_label (str | None): New origin name.
            prospect_ids (Sequence[UUID] | None): New stops in order; a stop that stays keeps
                its visit result.
            sent (frozenset[str]): Which of the nullable fields the request sent.

        Returns:
            Route: The route after the change.

        Raises:
            ForbiddenError: Without ``manage``.
            NotFoundError: When the route or the assignee does not exist.
            InvalidInputError: With bad values.
        """

        await self.managers.require(principal)
        route = await self.get(route_id)
        set_fields = set(sent & {"planned_for", "origin", "origin_label"})
        assignee_id = None
        if "assignee_email" in sent:
            assignee_id = await self._assignee(assignee_email)
            set_fields.add("assignee_id")
        if "origin" in sent:
            set_fields.add("origin_label")
        fields = RouteFields(
            name=_name(name) if name is not None else None,
            planned_for=planned_for,
            assignee_id=assignee_id,
            origin=_origin(origin),
            origin_label=(origin_label or "").strip()[:160] or None,
            set_fields=frozenset(set_fields),
        )
        stops: list[StopData] | None = None
        path: RoutePath | None = None
        if prospect_ids is not None:
            previous = {stop.data.prospect_id: stop.data for stop in route.stops}
            stops = []
            for stop in await self._stops(prospect_ids):
                kept = previous.get(stop.prospect_id)
                if kept is not None:
                    stop = replace(
                        stop,
                        visit_status=kept.visit_status,
                        visit_note=kept.visit_note,
                        visited_at=kept.visited_at,
                    )
                stops.append(stop)
        elif "origin" in sent:
            stops = [stop.data for stop in route.stops]
        if stops is not None:
            stops = [replace(stop, leg_distance_m=None, leg_duration_s=None) for stop in stops]
            path = RoutePath(geometry_status=NOT_COMPUTED)
        updated = await self.routes.update(route_id, fields, stops, path)
        if updated is None:
            raise NotFoundError("Ruta no encontrada")
        return updated

    async def compute(self, principal: Principal, route_id: UUID, optimize: bool) -> Route:
        """Order (when asked) and draw a route again (``manage``); spends routing quota.

        Args:
            principal (Principal): The caller.
            route_id (UUID): The route.
            optimize (bool): Reorder the stops for the shortest time.

        Returns:
            Route: The route with its new line.

        Raises:
            ForbiddenError: Without ``manage``.
            NotFoundError: When it does not exist.
        """

        await self.managers.require(principal)
        route = await self.get(route_id)
        stops, path = await self._plan(route.origin, [stop.data for stop in route.stops], optimize)
        updated = await self.routes.update(route_id, RouteFields(), stops, path)
        if updated is None:
            raise NotFoundError("Ruta no encontrada")
        return updated

    async def update_stop(
        self,
        principal: Principal,
        route_id: UUID,
        stop_id: UUID,
        *,
        visit_status: str | None = None,
        visit_note: str | None = None,
        set_note: bool = False,
    ) -> RouteStop:
        """Record the result of a visit (``manage`` or the route's assignee).

        Args:
            principal (Principal): The caller.
            route_id (UUID): The route.
            stop_id (UUID): The stop.
            visit_status (str | None): New status; also the prospect's.
            visit_note (str | None): New note.
            set_note (bool): Whether the note was sent (``None`` clears it).

        Returns:
            RouteStop: The stop after the change.

        Raises:
            ForbiddenError: For someone else's route without ``manage``.
            NotFoundError: When the route or stop does not exist.
            InvalidInputError: With an unknown status.
        """

        route = await self.get(route_id)
        if route.assignee_id != principal.user_id and not await self.managers.is_manager(principal):
            raise ForbiddenError("Solo quien gestiona el mapa o el responsable de la ruta")
        if visit_status is not None and visit_status not in VISIT_STATUSES:
            raise InvalidInputError(f"Estado de visita desconocido: {visit_status}")
        stop = await self.routes.update_stop(
            route_id,
            stop_id,
            visit_status,
            (visit_note or "").strip() or None,
            set_note,
        )
        if stop is None:
            raise NotFoundError("Parada no encontrada")
        return stop

    async def delete(self, principal: Principal, route_id: UUID) -> None:
        """Delete a route (``manage``).

        Args:
            principal (Principal): The caller.
            route_id (UUID): The route.

        Raises:
            ForbiddenError: Without ``manage``.
            NotFoundError: When it does not exist.
        """

        await self.managers.require(principal)
        if not await self.routes.delete(route_id):
            raise NotFoundError("Ruta no encontrada")


__all__ = ["MIN_STOPS", "RouteService"]
