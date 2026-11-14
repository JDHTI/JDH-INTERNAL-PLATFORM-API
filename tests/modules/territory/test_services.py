"""Use cases of the commercial map with in-memory ports."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    PayloadTooLargeError,
)
from app.modules.territory.domain.entities import Point, ProspectData
from tests.modules.territory.conftest import MANAGER, person, row, workbook

SQUARE = {
    "type": "Polygon",
    "coordinates": [[[-75.6, 6.2], [-75.5, 6.2], [-75.5, 6.3], [-75.6, 6.3], [-75.6, 6.2]]],
}


def _file():
    return workbook(
        {
            "Belén": [row("BEL-1", "Deli"), row("BEL-2", "Sin punto", latitude=None)],
            "Centro": [row("C-1", "Bar", 6.24, -75.57)],
        }
    )


# --- Prospects -------------------------------------------------------------------------------


async def test_a_load_simulates_then_applies_then_finds_nothing_new(
    prospect_service, prospects
) -> None:
    manager = person(MANAGER)

    simulated = await prospect_service.load(manager, "prospectos.xlsx", _file(), apply=False)
    assert not simulated.applied and simulated.to_create == 3 and not prospects.items
    assert (simulated.with_location, simulated.without_location) == (2, 1)

    applied = await prospect_service.load(manager, "prospectos.xlsx", _file(), apply=True)
    assert applied.applied and applied.load_id and len(prospects.items) == 3

    again = await prospect_service.load(manager, "prospectos.xlsx", _file(), apply=True)
    assert (again.to_create, again.to_update, again.unchanged) == (0, 0, 3)


async def test_a_point_moved_by_hand_survives_the_next_load(prospect_service, prospects) -> None:
    manager = person(MANAGER)
    await prospect_service.load(manager, "p.xlsx", _file(), apply=True)
    deli = prospects.by_external("BEL-1")

    moved = await prospect_service.update(manager, deli.id, latitude=6.21, longitude=-75.59)
    assert moved.data.location_quality == "manual"

    report = await prospect_service.load(manager, "p.xlsx", _file(), apply=True)
    assert report.unchanged == 3
    assert prospects.items[deli.id].data.location == Point(latitude=6.21, longitude=-75.59)


async def test_row_errors_block_the_load(prospect_service, prospects) -> None:
    content = workbook({"Belén": [row("BEL-1", "Uno"), row("BEL-1", "Otra vez")]})

    report = await prospect_service.load(person(MANAGER), "p.xlsx", content, apply=True)

    assert report.blocked and not report.applied and not prospects.items
    assert report.errors[0].field == "ID"


async def test_loading_needs_manage_an_xlsx_and_a_small_file(prospect_service) -> None:
    with pytest.raises(ForbiddenError):
        await prospect_service.load(person("lector@jdh.com.co"), "p.xlsx", _file(), apply=False)
    with pytest.raises(InvalidInputError, match="Formato"):
        await prospect_service.load(person(MANAGER), "p.csv", b"a,b", apply=False)
    prospect_service.max_upload_bytes = 10
    with pytest.raises(PayloadTooLargeError):
        await prospect_service.load(person(MANAGER), "p.xlsx", _file(), apply=False)


async def test_permissions_and_hand_edits(prospect_service, prospects) -> None:
    item = prospects.add(ProspectData(external_id="X-1", source_zone="Centro", name="Bar"))

    assert await prospect_service.permissions(person(MANAGER)) == (True, True)
    assert await prospect_service.permissions(person("lector@jdh.com.co")) == (True, False)
    assert await prospect_service.permissions(person("otro@jdh.com.co")) == (False, False)

    manager = person(MANAGER)
    with pytest.raises(InvalidInputError, match="juntas"):
        await prospect_service.update(manager, item.id, latitude=6.2)
    with pytest.raises(InvalidInputError, match="fuera de Colombia"):
        await prospect_service.update(manager, item.id, latitude=40.4, longitude=-3.7)
    with pytest.raises(ForbiddenError):
        await prospect_service.update(person("lector@jdh.com.co"), item.id, visit_status="visited")
    with pytest.raises(NotFoundError):
        await prospect_service.update(manager, uuid4(), visit_status="visited")

    visited = await prospect_service.update(
        manager, item.id, visit_status="interested", visit_note=" Pide catálogo ", set_note=True
    )
    assert visited.visit_status == "interested" and visited.visit_note == "Pide catálogo"


# --- Zones -----------------------------------------------------------------------------------


async def test_drawing_a_zone_checks_name_color_and_shape(zone_service, zones) -> None:
    manager = person(MANAGER)

    zone = await zone_service.create(manager, "  Laureles ", "green", SQUARE)
    assert zone.name == "Laureles" and zone.layer == "commercial"

    with pytest.raises(ConflictError):
        await zone_service.create(manager, "laureles", "blue", SQUARE)
    with pytest.raises(InvalidInputError, match="Color"):
        await zone_service.create(manager, "Poblado", "teal", SQUARE)
    outside = {
        "type": "Polygon",
        "coordinates": [[[-3.7, 40.4], [-3.6, 40.4], [-3.6, 40.5], [-3.7, 40.4]]],
    }
    with pytest.raises(InvalidInputError, match="fuera de Colombia"):
        await zone_service.create(manager, "Madrid", "red", outside)
    with pytest.raises(InvalidInputError, match="Polygon"):
        await zone_service.create(manager, "Punto", "red", {"type": "Point", "coordinates": [0, 0]})
    with pytest.raises(InvalidInputError, match="4 puntos"):
        await zone_service.create(
            manager,
            "Línea",
            "red",
            {"type": "Polygon", "coordinates": [[[-75.6, 6.2], [-75.5, 6.2]]]},
        )
    with pytest.raises(ForbiddenError):
        await zone_service.create(person("lector@jdh.com.co"), "Sabaneta", "red", SQUARE)


async def test_adopting_and_protecting_official_zones(zone_service, zones) -> None:
    manager = person(MANAGER)
    comuna = zones.add("Comuna 11 - Laureles Estadio", layer="official", code="11")

    adopted = await zone_service.adopt(manager, "Laureles", "green", [comuna.id, comuna.id])
    assert adopted.layer == "commercial"

    with pytest.raises(InvalidInputError, match="no es oficial"):
        await zone_service.adopt(manager, "Otra", "red", [adopted.id])
    with pytest.raises(InvalidInputError, match="oficiales no se editan"):
        await zone_service.update(manager, comuna.id, name="Cambio")
    with pytest.raises(InvalidInputError, match="oficiales no se editan"):
        await zone_service.delete(manager, comuna.id)

    renamed = await zone_service.update(manager, adopted.id, name="Laureles Estadio", color="pink")
    assert (renamed.name, renamed.color) == ("Laureles Estadio", "pink")
    await zone_service.delete(manager, adopted.id)
    with pytest.raises(NotFoundError):
        await zone_service.get(adopted.id)
    with pytest.raises(InvalidInputError, match="Capa"):
        await zone_service.list("other", None, True)


# --- Routes ----------------------------------------------------------------------------------


def _located(prospects, count: int):
    return [
        prospects.add(
            ProspectData(
                external_id=f"P-{i}",
                source_zone="Belén",
                name=f"Prospecto {i}",
                location=Point(latitude=6.2 + i / 100, longitude=-75.6),
                location_quality="exact",
            )
        )
        for i in range(count)
    ]


async def test_a_route_is_ordered_and_drawn_by_road(
    route_service, prospects, routing, users
) -> None:
    stops = _located(prospects, 3)
    seller = users.add("vendedor@jdh.com.co")
    origin = Point(latitude=6.25, longitude=-75.58)

    route = await route_service.create(
        person(MANAGER),
        name="Belén martes",
        prospect_ids=[s.id for s in stops],
        assignee_email="vendedor@jdh.com.co",
        origin=origin,
        origin_label="Bodega",
    )

    assert routing.calls == ["optimize", "directions"]
    assert [s.data.name for s in route.stops] == ["Prospecto 2", "Prospecto 1", "Prospecto 0"]
    assert route.path.geometry_status == "road" and route.path.distance_m == 6000
    assert route.path.geometry["coordinates"][0] == [-75.58, 6.25]
    # With an origin, the first leg arrives at the first stop.
    assert [s.data.leg_distance_m for s in route.stops] == [1000, 2000, 3000]
    assert route.assignee_id == seller.id and route.origin_label == "Bodega"


async def test_without_origin_the_first_stop_stays_first(route_service, prospects) -> None:
    stops = _located(prospects, 3)

    route = await route_service.create(
        person(MANAGER), name="Sin origen", prospect_ids=[s.id for s in stops]
    )

    assert route.stops[0].data.name == "Prospecto 0"
    assert route.stops[0].data.leg_distance_m is None
    assert route.stops[1].data.leg_distance_m == 1000


async def test_when_routing_fails_the_route_is_saved_in_straight_lines(
    route_service, prospects, routing
) -> None:
    routing.fail = "Se agotó la cuota del día"
    stops = _located(prospects, 3)

    route = await route_service.create(
        person(MANAGER), name="Recta", prospect_ids=[s.id for s in reversed(stops)]
    )

    assert route.path.geometry_status == "straight"
    assert route.path.routing_note == "Se agotó la cuota del día"
    assert route.path.duration_s is None and route.path.distance_m > 0
    # The greedy order from the first stop (P-2) walks to P-1, then P-0.
    assert [s.data.name for s in route.stops] == ["Prospecto 2", "Prospecto 1", "Prospecto 0"]
    assert routing.calls == ["optimize"]


async def test_routing_off_never_calls_the_service(route_service, prospects, routing) -> None:
    routing.enabled = False
    stops = _located(prospects, 2)

    route = await route_service.create(
        person(MANAGER), name="Sin llave", prospect_ids=[s.id for s in stops], optimize=False
    )

    assert routing.calls == [] and route.path.geometry_status == "straight"
    assert "no está configurado" in route.path.routing_note


async def test_route_stops_are_checked(route_service, prospects, users) -> None:
    manager = person(MANAGER)
    located = _located(prospects, 2)
    unlocated = prospects.add(ProspectData(external_id="U-1", source_zone="Centro", name="Bar"))

    with pytest.raises(InvalidInputError, match="entre 2"):
        await route_service.create(manager, name="Una", prospect_ids=[located[0].id])
    with pytest.raises(InvalidInputError, match="dos veces"):
        await route_service.create(manager, name="Doble", prospect_ids=[located[0].id] * 2)
    with pytest.raises(InvalidInputError, match="Sin ubicación"):
        await route_service.create(manager, name="X", prospect_ids=[located[0].id, unlocated.id])
    with pytest.raises(InvalidInputError, match="no encontrados"):
        await route_service.create(manager, name="X", prospect_ids=[located[0].id, uuid4()])
    with pytest.raises(NotFoundError):
        await route_service.create(
            manager,
            name="X",
            prospect_ids=[p.id for p in located],
            assignee_email="nadie@jdh.com.co",
        )
    with pytest.raises(ForbiddenError):
        await route_service.create(
            person("lector@jdh.com.co"), name="X", prospect_ids=[p.id for p in located]
        )


async def test_changing_stops_leaves_the_route_pending_and_keeps_visits(
    route_service, prospects, routing
) -> None:
    manager = person(MANAGER)
    stops = _located(prospects, 3)
    route = await route_service.create(
        manager, name="R", prospect_ids=[s.id for s in stops], optimize=False
    )
    first = route.stops[0]
    await route_service.update_stop(manager, route.id, first.id, visit_status="visited")

    changed = await route_service.update(
        manager,
        route.id,
        prospect_ids=[stops[0].id, stops[2].id],
        planned_for=None,
        sent=frozenset({"prospect_ids", "planned_for"}),
    )

    assert changed.path.geometry_status == "pending" and len(changed.stops) == 2
    assert changed.stops[0].data.visit_status == "visited"
    assert changed.stops[1].data.leg_distance_m is None

    computed = await route_service.compute(manager, route.id, optimize=False)
    assert computed.path.geometry_status == "road"


async def test_only_managers_or_the_assignee_record_visits(route_service, prospects, users) -> None:
    seller = users.add("vendedor@jdh.com.co")
    stops = _located(prospects, 2)
    route = await route_service.create(
        person(MANAGER),
        name="R",
        prospect_ids=[s.id for s in stops],
        assignee_email=seller.email,
    )
    stop = route.stops[0]
    assignee = person(seller.email)
    assignee = assignee.__class__(**{**assignee.__dict__, "user_id": seller.id})

    marked = await route_service.update_stop(
        assignee, route.id, stop.id, visit_status="interested", visit_note="Volver", set_note=True
    )
    assert (marked.data.visit_status, marked.data.visit_note) == ("interested", "Volver")

    with pytest.raises(ForbiddenError):
        await route_service.update_stop(person("otro@jdh.com.co"), route.id, stop.id)
    with pytest.raises(InvalidInputError):
        await route_service.update_stop(assignee, route.id, stop.id, visit_status="maybe")
    with pytest.raises(NotFoundError):
        await route_service.update_stop(assignee, route.id, uuid4(), visit_status="visited")

    await route_service.delete(person(MANAGER), route.id)
    with pytest.raises(NotFoundError):
        await route_service.get(route.id)
