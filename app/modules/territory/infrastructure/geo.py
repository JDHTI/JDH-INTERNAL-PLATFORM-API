"""SQL pieces the repositories of the commercial map share."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import ColumnElement, String, bindparam, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.territory.domain.entities import Point
from app.modules.territory.domain.vocabulary import COMMERCIAL
from app.modules.territory.infrastructure.models import GeoZoneModel as Z
from app.modules.territory.infrastructure.models import ProspectModel as P

SRID = 4326
# Simplification of the painted polygons, in degrees (about 5 m at Colombia's latitude).
SIMPLIFY_TOLERANCE = 0.00005


def point(value: Point) -> ColumnElement[Any]:
    """A PostGIS point from a WGS84 point."""

    return func.ST_SetSRID(func.ST_MakePoint(value.longitude, value.latitude), SRID)


def from_geojson(geometry: dict[str, Any]) -> ColumnElement[Any]:
    """A PostGIS geometry from a GeoJSON one, as it comes (no repair)."""

    text = bindparam(None, json.dumps(geometry), type_=String)
    return func.ST_SetSRID(func.ST_GeomFromGeoJSON(text), SRID)


def repaired_area(geometry: ColumnElement[Any]) -> ColumnElement[Any]:
    """A valid ``MULTIPOLYGON`` with only the polygons of a geometry (empty if none)."""

    return func.ST_Multi(func.ST_CollectionExtract(func.ST_MakeValid(geometry), 3))


def as_geojson(geometry: ColumnElement[Any], simplify: bool = False) -> ColumnElement[Any]:
    """The GeoJSON text of a geometry, 6 decimals (about 10 cm)."""

    if simplify:
        geometry = func.ST_SimplifyPreserveTopology(geometry, SIMPLIFY_TOLERANCE)
    return func.ST_AsGeoJSON(geometry, 6)


def parse_geojson(text: str | None) -> dict[str, Any] | None:
    """The GeoJSON object PostGIS returned, or ``None``."""

    return json.loads(text) if text else None


def _zone_of(location: ColumnElement[Any]) -> ColumnElement[Any]:
    """The commercial zone that covers a point; the smallest wins when several do."""

    return (
        select(Z.id)
        .where(Z.layer == COMMERCIAL, func.ST_Covers(Z.geom, location))
        .order_by(func.ST_Area(Z.geom), Z.id)
        .limit(1)
        .scalar_subquery()
    )


async def place_prospects(session: AsyncSession, *where: ColumnElement[bool]) -> None:
    """Put each prospect in the commercial zone that covers its point.

    Only rows whose zone changes are written, and their ``updated_at`` is kept: moving a zone
    does not edit the prospect.

    Args:
        session (AsyncSession): Open session; the caller commits.
        *where (ColumnElement[bool]): Narrow it to some prospects (all by default).
    """

    zone = _zone_of(P.location)
    await session.execute(
        update(P)
        .where(P.commercial_zone_id.is_distinct_from(zone), *where)
        .values(commercial_zone_id=zone, updated_at=P.updated_at)
        .execution_options(synchronize_session=False)
    )


__all__ = [
    "SIMPLIFY_TOLERANCE",
    "SRID",
    "as_geojson",
    "from_geojson",
    "parse_geojson",
    "place_prospects",
    "point",
    "repaired_area",
]
