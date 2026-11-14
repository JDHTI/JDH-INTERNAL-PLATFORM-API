"""PostgreSQL store of the visit routes and their stops."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.modules.territory.domain.entities import (
    Point,
    Route,
    RouteFields,
    RoutePage,
    RoutePath,
    RouteStop,
    RouteSummary,
    StopData,
)
from app.modules.territory.domain.vocabulary import PENDING
from app.modules.territory.infrastructure import geo
from app.modules.territory.infrastructure.models import ProspectModel as P
from app.modules.territory.infrastructure.models import VisitRouteModel as R
from app.modules.territory.infrastructure.models import VisitRouteStopModel as S

_STOP_COLUMNS = (
    S.id,
    S.position,
    S.prospect_id,
    S.name,
    S.address,
    S.visit_status,
    S.visit_note,
    S.visited_at,
    S.leg_distance_m,
    S.leg_duration_s,
    func.ST_Y(S.location).label("latitude"),
    func.ST_X(S.location).label("longitude"),
)


def _stop(row: Any) -> RouteStop:
    return RouteStop(
        id=row.id,
        position=row.position,
        data=StopData(
            prospect_id=row.prospect_id,
            name=row.name,
            address=row.address,
            location=Point(latitude=row.latitude, longitude=row.longitude),
            visit_status=row.visit_status,
            visit_note=row.visit_note,
            visited_at=row.visited_at,
            leg_distance_m=row.leg_distance_m,
            leg_duration_s=row.leg_duration_s,
        ),
    )


def _path_values(path: RoutePath) -> dict[str, Any]:
    return {
        "geometry_status": path.geometry_status,
        "path": geo.from_geojson(path.geometry) if path.geometry else None,
        "distance_m": path.distance_m,
        "duration_s": path.duration_s,
        "routing_note": path.routing_note[:255] if path.routing_note else None,
    }


def _field_values(fields: RouteFields, creating: bool) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if fields.name is not None:
        values["name"] = fields.name.strip()
    for key in ("planned_for", "assignee_id", "origin_label"):
        if creating or key in fields.set_fields:
            values[key] = getattr(fields, key)
    if creating or "origin" in fields.set_fields:
        values["origin"] = geo.point(fields.origin) if fields.origin else None
    return values


async def _replace_stops(session: AsyncSession, route_id: UUID, stops: Sequence[StopData]) -> None:
    await session.execute(delete(S).where(S.route_id == route_id))
    if not stops:
        return
    await session.execute(
        insert(S).values(
            [
                {
                    "route_id": route_id,
                    "position": position,
                    "prospect_id": stop.prospect_id,
                    "name": stop.name[:255],
                    "address": stop.address,
                    "location": geo.point(stop.location),
                    "visit_status": stop.visit_status,
                    "visit_note": stop.visit_note,
                    "visited_at": stop.visited_at,
                    "leg_distance_m": stop.leg_distance_m,
                    "leg_duration_s": stop.leg_duration_s,
                }
                for position, stop in enumerate(stops, 1)
            ]
        )
    )


class SqlAlchemyRouteRepository:
    """Visit routes: an origin, ordered stops and the line that joins them."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Application database.
        """

        self.database = database

    async def page(self, assignee_id: UUID | None, page: int, size: int) -> RoutePage:
        """Routes, the latest planned first (undated ones last), then the newest.

        Args:
            assignee_id (UUID | None): Only this person's routes.
            page (int): 1-based page.
            size (int): Page size.

        Returns:
            RoutePage: The page and the total.
        """

        stops = (
            select(
                S.route_id,
                func.count().label("stop_count"),
                func.count().filter(S.visit_status != PENDING).label("visited_count"),
            )
            .group_by(S.route_id)
            .subquery()
        )
        conditions = [R.assignee_id == assignee_id] if assignee_id is not None else []
        async with self.database.session_factory() as session:
            total = await session.scalar(select(func.count()).select_from(R).where(*conditions))
            rows = await session.execute(
                select(
                    R.id,
                    R.name,
                    R.planned_for,
                    R.assignee_id,
                    R.geometry_status,
                    R.distance_m,
                    R.duration_s,
                    R.created_by_id,
                    R.created_at,
                    R.updated_at,
                    func.coalesce(stops.c.stop_count, 0).label("stop_count"),
                    func.coalesce(stops.c.visited_count, 0).label("visited_count"),
                )
                .outerjoin(stops, stops.c.route_id == R.id)
                .where(*conditions)
                .order_by(R.planned_for.desc().nulls_last(), R.created_at.desc())
                .limit(size)
                .offset((page - 1) * size)
            )
            items = [
                RouteSummary(
                    id=row.id,
                    name=row.name,
                    planned_for=row.planned_for,
                    assignee_id=row.assignee_id,
                    stop_count=row.stop_count,
                    visited_count=row.visited_count,
                    geometry_status=row.geometry_status,
                    distance_m=row.distance_m,
                    duration_s=row.duration_s,
                    created_by_id=row.created_by_id,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in rows
            ]
        return RoutePage(items=items, total=total or 0, page=page, size=size)

    async def get(self, route_id: UUID) -> Route | None:
        """One route with its stops in order.

        Args:
            route_id (UUID): Its id.

        Returns:
            Route | None: The route, or ``None``.
        """

        async with self.database.session_factory() as session:
            row = (
                await session.execute(
                    select(
                        R.id,
                        R.name,
                        R.planned_for,
                        R.assignee_id,
                        R.origin_label,
                        R.geometry_status,
                        R.distance_m,
                        R.duration_s,
                        R.routing_note,
                        R.created_by_id,
                        R.created_at,
                        R.updated_at,
                        func.ST_Y(R.origin).label("origin_latitude"),
                        func.ST_X(R.origin).label("origin_longitude"),
                        geo.as_geojson(R.path).label("geojson"),
                    ).where(R.id == route_id)
                )
            ).one_or_none()
            if row is None:
                return None
            stops = await session.execute(
                select(*_STOP_COLUMNS).where(S.route_id == route_id).order_by(S.position)
            )
            origin = (
                Point(latitude=row.origin_latitude, longitude=row.origin_longitude)
                if row.origin_latitude is not None
                else None
            )
            return Route(
                id=row.id,
                name=row.name,
                planned_for=row.planned_for,
                assignee_id=row.assignee_id,
                origin=origin,
                origin_label=row.origin_label,
                path=RoutePath(
                    geometry_status=row.geometry_status,
                    geometry=geo.parse_geojson(row.geojson),
                    distance_m=row.distance_m,
                    duration_s=row.duration_s,
                    routing_note=row.routing_note,
                ),
                stops=[_stop(stop) for stop in stops],
                created_by_id=row.created_by_id,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

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

        async with self.database.session_factory() as session:
            route_id = await session.scalar(
                insert(R)
                .values(
                    **_field_values(fields, creating=True),
                    **_path_values(path),
                    created_by_id=created_by_id,
                )
                .returning(R.id)
            )
            if route_id is None:
                raise RuntimeError("visit_routes did not return the new id")
            await _replace_stops(session, route_id, stops)
            await session.commit()
        route = await self.get(route_id)
        if route is None:
            raise RuntimeError("the new route is missing")
        return route

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

        values = _field_values(fields, creating=False)
        if path is not None:
            values.update(_path_values(path))
        async with self.database.session_factory() as session:
            found = await session.scalar(
                update(R)
                .where(R.id == route_id)
                .values(**values, updated_at=func.now())
                .returning(R.id)
            )
            if found is None:
                return None
            if stops is not None:
                await _replace_stops(session, route_id, stops)
            await session.commit()
        return await self.get(route_id)

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

        values: dict[str, Any] = {}
        if visit_status is not None:
            values["visit_status"] = visit_status
            values["visited_at"] = None if visit_status == PENDING else func.now()
        if set_note:
            values["visit_note"] = note
        async with self.database.session_factory() as session:
            if values:
                prospect_id = await session.execute(
                    update(S)
                    .where(S.id == stop_id, S.route_id == route_id)
                    .values(**values)
                    .returning(S.prospect_id)
                )
                found = prospect_id.one_or_none()
                if found is None:
                    return None
                if found.prospect_id is not None:
                    await session.execute(
                        update(P).where(P.id == found.prospect_id).values(**values)
                    )
                await session.commit()
            row = (
                await session.execute(
                    select(*_STOP_COLUMNS).where(S.id == stop_id, S.route_id == route_id)
                )
            ).one_or_none()
            return _stop(row) if row else None

    async def delete(self, route_id: UUID) -> bool:
        """Delete a route with its stops.

        Args:
            route_id (UUID): The route.

        Returns:
            bool: ``True`` when it existed.
        """

        async with self.database.session_factory() as session:
            found = await session.scalar(delete(R).where(R.id == route_id).returning(R.id))
            await session.commit()
            return found is not None


__all__ = ["SqlAlchemyRouteRepository"]
