"""Route math that needs no service: straight-line distances and a greedy visiting order.

Used when the routing service is off, out of quota or down, so a route can always be saved.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from app.modules.territory.domain.entities import Point

_EARTH_RADIUS_M = 6_371_000.0


def straight_distance(a: Point, b: Point) -> float:
    """Great-circle distance in meters.

    Args:
        a (Point): From.
        b (Point): To.

    Returns:
        float: Meters, as the crow flies.
    """

    lat1, lat2 = math.radians(a.latitude), math.radians(b.latitude)
    d_lat = lat2 - lat1
    d_lon = math.radians(b.longitude - a.longitude)
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(h))


def nearest_neighbor_order(start: Point, stops: Sequence[Point]) -> list[int]:
    """A short visiting order: always the closest stop not yet visited.

    Args:
        start (Point): Where the visit starts.
        stops (Sequence[Point]): Points to visit.

    Returns:
        list[int]: Indexes of ``stops`` in visiting order.
    """

    pending = list(range(len(stops)))
    order: list[int] = []
    current = start
    while pending:
        closest = min(pending, key=lambda index: straight_distance(current, stops[index]))
        order.append(closest)
        pending.remove(closest)
        current = stops[closest]
    return order


def line(points: Sequence[Point]) -> dict[str, object]:
    """A GeoJSON ``LineString`` through the points.

    Args:
        points (Sequence[Point]): Two or more points.

    Returns:
        dict[str, object]: The line, ``[lon, lat]`` pairs.
    """

    return {
        "type": "LineString",
        "coordinates": [[point.longitude, point.latitude] for point in points],
    }


__all__ = ["line", "nearest_neighbor_order", "straight_distance"]
