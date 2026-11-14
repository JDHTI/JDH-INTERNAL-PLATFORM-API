"""Closed values of the commercial map, shared by the API, the tables and the UI."""

from __future__ import annotations

MODULE_KEY = "mapa-comercial"

# exact: the file's point; approximate: a point many rows share (geocoded to a centroid);
# manual: moved by hand on the map (a new load does not overwrite it); missing: no point.
EXACT = "exact"
APPROXIMATE = "approximate"
MANUAL = "manual"
MISSING = "missing"
LOCATION_QUALITIES = (EXACT, APPROXIMATE, MANUAL, MISSING)
# Rows of a file sharing one point this many times or more are approximate.
SHARED_POINT_THRESHOLD = 3

AREA_STATUSES = ("inside", "outside", "unlocated")

PENDING = "pending"
VISIT_STATUSES = (PENDING, "visited", "interested", "discarded")

OFFICIAL = "official"
COMMERCIAL = "commercial"
ZONE_LAYERS = (OFFICIAL, COMMERCIAL)
ZONE_LEVELS = ("department", "municipality", "commune", "neighborhood", "custom")
# The categorical palette of the UI (``--project-*`` tokens).
ZONE_COLORS = ("gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red")

ROAD = "road"
STRAIGHT = "straight"
NOT_COMPUTED = "pending"
GEOMETRY_STATUSES = (ROAD, STRAIGHT, NOT_COMPUTED)

# Mainland Colombia plus San Andrés and Providencia, with a margin.
COLOMBIA_BOUNDS = (-82.0, -4.3, -66.8, 13.6)  # west, south, east, north


def in_colombia(latitude: float, longitude: float) -> bool:
    """Whether a point falls inside the bounding box of Colombia.

    Args:
        latitude (float): WGS84 latitude.
        longitude (float): WGS84 longitude.

    Returns:
        bool: ``True`` when inside the box.
    """

    west, south, east, north = COLOMBIA_BOUNDS
    return south <= latitude <= north and west <= longitude <= east


__all__ = [
    "APPROXIMATE",
    "AREA_STATUSES",
    "COLOMBIA_BOUNDS",
    "COMMERCIAL",
    "EXACT",
    "GEOMETRY_STATUSES",
    "LOCATION_QUALITIES",
    "MANUAL",
    "MISSING",
    "MODULE_KEY",
    "NOT_COMPUTED",
    "OFFICIAL",
    "PENDING",
    "ROAD",
    "SHARED_POINT_THRESHOLD",
    "STRAIGHT",
    "VISIT_STATUSES",
    "ZONE_COLORS",
    "ZONE_LAYERS",
    "ZONE_LEVELS",
    "in_colombia",
]
