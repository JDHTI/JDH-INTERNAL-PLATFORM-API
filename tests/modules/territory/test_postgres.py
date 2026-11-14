"""Opt-in: the commercial map on PostgreSQL + PostGIS (TERRITORY_TEST_DATABASE_URL).

Point it to a migrated database of its own: every test starts by emptying the module's tables.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from app.core.errors import InvalidInputError
from app.infrastructure.database import Database
from app.modules.territory.domain.entities import (
    LoadCounts,
    OfficialZoneData,
    Point,
    ProspectChange,
    ProspectData,
    ProspectFilters,
    RouteFields,
    RoutePath,
    StopData,
)
from app.modules.territory.infrastructure.prospect_repository import (
    SqlAlchemyProspectRepository,
)
from app.modules.territory.infrastructure.route_repository import SqlAlchemyRouteRepository
from app.modules.territory.infrastructure.zone_repository import SqlAlchemyZoneRepository

pytestmark = pytest.mark.skipif(
    not os.getenv("TERRITORY_TEST_DATABASE_URL"),
    reason="Dedicated PostGIS integration URL not set",
)


def square(west: float, south: float, size: float) -> dict:
    east, north = west + size, south + size
    return {
        "type": "Polygon",
        "coordinates": [
            [[west, south], [east, south], [east, north], [west, north], [west, south]]
        ],
    }


def prospect(external_id: str, latitude: float | None, longitude: float = -75.6, **values):
    location = Point(latitude, longitude) if latitude is not None else None
    return ProspectData(
        external_id=external_id,
        source_zone=values.pop("source_zone", "Belén"),
        name=values.pop("name", f"Local {external_id}"),
        location=location,
        location_quality="exact" if location else "missing",
        **values,
    )


@pytest.fixture
async def database():
    database = Database(os.environ["TERRITORY_TEST_DATABASE_URL"])
    async with database.session_factory() as session:
        await session.execute(
            text(
                "TRUNCATE visit_route_stops, visit_routes, prospects, prospect_loads, geo_zones "
                "CASCADE"
            )
        )
        await session.commit()
    try:
        yield database
    finally:
        await database.dispose()


async def test_load_place_in_zones_and_keep_hand_edits(database) -> None:
    prospects = SqlAlchemyProspectRepository(database)
    zones = SqlAlchemyZoneRepository(database)
    rows = [
        prospect("A", 6.21, -75.59, category="Bar"),
        prospect("B", 6.25, -75.55),
        prospect("C", None, source_zone="Centro"),
    ]

    await prospects.apply_load("p.xlsx", rows, LoadCounts(3, 0, 0), None, "jefe@jdh.com.co")
    stored = await prospects.stored(["A", "B", "C", "Z"])
    assert set(stored) == {"A", "B", "C"} and stored["A"].location == Point(6.21, -75.59)

    # A big zone and a small one inside it: the small one wins.
    big = await zones.create("Valle", "blue", square(-75.7, 6.1, 0.3), (), None)
    small = await zones.create("Belén", "green", square(-75.6, 6.2, 0.02), (), None)
    a_page = await prospects.page(ProspectFilters(q="local a"), 1, 10)
    assert a_page.items[0].commercial_zone.name == "Belén"
    assert (await prospects.page(ProspectFilters(zone_id=big.id), 1, 10)).total == 1
    listed = {zone.name: zone.prospect_count for zone in await zones.list("commercial", None, True)}
    assert listed == {"Valle": 1, "Belén": 1}

    # Moving A by hand: manual, out of the small zone; a new load keeps the point.
    a_id = a_page.items[0].id
    moved = await prospects.update(a_id, ProspectChange(location=Point(6.28, -75.65)))
    assert moved.data.location_quality == "manual" and moved.commercial_zone.id == big.id
    await prospects.apply_load("p.xlsx", rows, LoadCounts(0, 0, 3), None, None)
    again = await prospects.get(a_id)
    assert again.data.location == Point(6.28, -75.65) and again.data.location_quality == "manual"

    # Deleting the big zone leaves A without zone; B too.
    assert await zones.delete(big.id)
    assert (await prospects.get(a_id)).commercial_zone is None
    assert (await zones.get(small.id)).prospect_count == 0

    facets = await prospects.facets()
    assert (facets.total, facets.located, facets.unlocated) == (3, 2, 1)
    assert [(f.value, f.count) for f in facets.source_zones] == [("Belén", 2), ("Centro", 1)]
    points = await prospects.map_points(ProspectFilters(category="Bar"))
    assert [p.external_id for p in points] == ["A"]
    assert (await prospects.page(ProspectFilters(located=False), 1, 10)).items[
        0
    ].data.external_id == "C"
    assert (await prospects.loads(5))[0].rows_read == 3


async def test_official_zones_adopt_and_bad_shapes(database) -> None:
    zones = SqlAlchemyZoneRepository(database)
    items = [
        OfficialZoneData("commune", "Comuna 11", "11", square(-75.6, 6.24, 0.02)),
        OfficialZoneData("commune", "Comuna 12", "12", square(-75.58, 6.24, 0.02)),
    ]

    assert await zones.upsert_official(items, "GeoMedellín") == (2, 0)
    assert await zones.upsert_official(items[:1], "GeoMedellín") == (0, 1)
    # Past five runs PostgreSQL plans the statement generically: it must still find the index.
    many = [
        OfficialZoneData(
            "neighborhood", f"Barrio {i}", f"B{i}", square(-75.6 + i / 100, 6.3, 0.005)
        )
        for i in range(8)
    ]
    assert await zones.upsert_official(many, "GeoMedellín") == (8, 0)
    assert await zones.upsert_official(many, "GeoMedellín") == (0, 8)
    official = await zones.list("official", "commune", False)
    assert [z.name for z in official] == ["Comuna 11", "Comuna 12"]
    assert await zones.official_count([z.id for z in official]) == 2

    adopted = await zones.create("Laureles", "green", None, [z.id for z in official], None)
    assert adopted.geometry["type"] == "MultiPolygon" and adopted.layer == "commercial"
    assert await zones.name_taken("laureles") and not await zones.name_taken("laureles", adopted.id)

    # A bow tie is repaired into two triangles; a degenerate shape has no area.
    bow_tie = {
        "type": "Polygon",
        "coordinates": [[[-75.6, 6.2], [-75.5, 6.3], [-75.5, 6.2], [-75.6, 6.3], [-75.6, 6.2]]],
    }
    repaired = await zones.create("Moño", "red", bow_tie, (), None)
    assert len(repaired.geometry["coordinates"]) == 2
    flat = {
        "type": "Polygon",
        "coordinates": [[[-75.6, 6.2], [-75.5, 6.2], [-75.4, 6.2], [-75.6, 6.2]]],
    }
    with pytest.raises(InvalidInputError):
        await zones.create("Plana", "red", flat, (), None)
    updated = await zones.update(repaired.id, "Moño 2", None, square(-75.6, 6.2, 0.01))
    assert updated.name == "Moño 2" and len(updated.geometry["coordinates"]) == 1


async def test_an_official_zone_counts_and_filters_the_prospects_inside(database) -> None:
    prospects = SqlAlchemyProspectRepository(database)
    zones = SqlAlchemyZoneRepository(database)
    rows = [prospect("A", 6.205, -75.565), prospect("B", 6.21, -75.56), prospect("C", 6.3)]
    await prospects.apply_load("p.xlsx", rows, LoadCounts(3, 0, 0), None, None)
    poblado = OfficialZoneData("commune", "El Poblado", "14", square(-75.58, 6.19, 0.03))
    await zones.upsert_official([poblado], "OSM")

    (zone,) = await zones.list("official", "commune", True)
    assert zone.prospect_count == 2

    inside = await prospects.map_points(ProspectFilters(zone_id=zone.id))
    assert sorted(p.external_id for p in inside) == ["A", "B"]
    # Official zones do not place prospects: only commercial ones do.
    assert all(p.commercial_zone_id is None for p in inside)


async def test_routes_store_the_line_and_record_visits_on_the_prospect(database) -> None:
    prospects = SqlAlchemyProspectRepository(database)
    routes = SqlAlchemyRouteRepository(database)
    await prospects.apply_load(
        "p.xlsx", [prospect("A", 6.21), prospect("B", 6.22)], LoadCounts(2, 0, 0), None, None
    )
    stored = {p.data.external_id: p for p in (await prospects.page(ProspectFilters(), 1, 10)).items}
    stops = [
        StopData(
            prospect_id=stored[key].id, name=key, address=None, location=stored[key].data.location
        )
        for key in ("A", "B")
    ]
    path = RoutePath(
        geometry_status="road",
        geometry={"type": "LineString", "coordinates": [[-75.6, 6.21], [-75.6, 6.22]]},
        distance_m=1200,
        duration_s=180,
    )

    route = await routes.create(
        RouteFields(name="Martes", origin=Point(6.2, -75.61), origin_label="Bodega"),
        stops,
        path,
        None,
    )
    assert route.origin == Point(6.2, -75.61) and route.path.distance_m == 1200
    assert route.path.geometry["coordinates"][1] == [-75.6, 6.22]
    assert [s.position for s in route.stops] == [1, 2]

    stop = route.stops[0]
    marked = await routes.update_stop(route.id, stop.id, "visited", "Atendió el dueño", True)
    assert marked.data.visit_status == "visited" and marked.data.visited_at is not None
    assert (await prospects.get(stored["A"].id)).visit_status == "visited"

    page = await routes.page(None, 1, 10)
    assert page.total == 1 and page.items[0].visited_count == 1 and page.items[0].stop_count == 2

    cleared = await routes.update(
        route.id,
        RouteFields(origin=None, set_fields=frozenset({"origin", "origin_label"})),
        list(reversed(stops)),
        RoutePath(geometry_status="pending"),
    )
    assert cleared.origin is None and cleared.path.geometry is None
    assert cleared.stops[0].data.name == "B" and cleared.path.geometry_status == "pending"
    assert await routes.delete(route.id) and await routes.get(route.id) is None
