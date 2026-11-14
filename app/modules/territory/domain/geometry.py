"""Checks on the GeoJSON polygons a person draws or a file brings, before PostGIS sees them."""

from __future__ import annotations

import math
from typing import Any

from app.core.errors import InvalidInputError
from app.modules.territory.domain.vocabulary import in_colombia

MAX_VERTICES = 50_000


def _position(value: Any) -> tuple[float, float]:
    if not isinstance(value, list | tuple) or len(value) < 2:
        raise InvalidInputError("La geometría tiene un punto mal formado")
    longitude, latitude = value[0], value[1]
    if not all(
        isinstance(item, int | float) and not isinstance(item, bool) and math.isfinite(item)
        for item in (longitude, latitude)
    ):
        raise InvalidInputError("La geometría tiene coordenadas que no son números")
    return float(longitude), float(latitude)


def _polygon(rings: Any) -> int:
    if not isinstance(rings, list) or not rings:
        raise InvalidInputError("Un polígono necesita al menos un anillo")
    vertices = 0
    for ring in rings:
        if not isinstance(ring, list) or len(ring) < 4:
            raise InvalidInputError("Cada anillo del polígono necesita al menos 4 puntos")
        for item in ring:
            longitude, latitude = _position(item)
            if not in_colombia(latitude, longitude):
                raise InvalidInputError("La zona tiene puntos fuera de Colombia")
        vertices += len(ring)
    return vertices


def check_area(geometry: Any, max_vertices: int = MAX_VERTICES) -> dict[str, Any]:
    """Validate a GeoJSON ``Polygon`` or ``MultiPolygon`` inside Colombia.

    Self-intersections are not checked here: PostGIS repairs them (``ST_MakeValid``).

    Args:
        geometry (Any): The GeoJSON geometry object.
        max_vertices (int): Largest number of points accepted.

    Returns:
        dict[str, Any]: The same geometry.

    Raises:
        InvalidInputError: When it is not a polygon, is malformed, too large or outside
            Colombia.
    """

    if not isinstance(geometry, dict):
        raise InvalidInputError("La geometría debe ser un objeto GeoJSON")
    kind, coordinates = geometry.get("type"), geometry.get("coordinates")
    if kind == "Polygon":
        vertices = _polygon(coordinates)
    elif kind == "MultiPolygon":
        if not isinstance(coordinates, list) or not coordinates:
            raise InvalidInputError("El multipolígono está vacío")
        vertices = sum(_polygon(polygon) for polygon in coordinates)
    else:
        raise InvalidInputError("La zona debe ser un Polygon o un MultiPolygon de GeoJSON")
    if vertices > max_vertices:
        raise InvalidInputError(f"La zona tiene demasiados puntos (máximo {max_vertices})")
    return {"type": kind, "coordinates": coordinates}


__all__ = ["MAX_VERTICES", "check_area"]
