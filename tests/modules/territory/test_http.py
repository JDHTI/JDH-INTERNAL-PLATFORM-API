"""HTTP contract of ``/territory`` with the real permission gates of ``mapa-comercial``."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.areas.application.dto import CreateAreaCommand
from app.modules.areas.application.service import AreaService
from app.modules.identity.presentation.principal import get_current_principal
from tests.modules.fakes import FakeAreaRepository, FakeDirectory
from tests.modules.territory.conftest import (
    FakeProspects,
    FakeRoutes,
    FakeRouting,
    FakeZones,
    row,
    workbook,
)

# The gates of this module are the real ones (see tests/conftest.py).
REAL_PERMISSION_GATES = True
BASE = "/api/v1/territory"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SQUARE = {
    "type": "Polygon",
    "coordinates": [[[-75.6, 6.2], [-75.5, 6.2], [-75.5, 6.3], [-75.6, 6.3], [-75.6, 6.2]]],
}


class World:
    def __init__(self) -> None:
        self.directory = FakeDirectory()
        self.areas = AreaService(FakeAreaRepository(), self.directory)
        self.admin = self.directory.sign_in("analista.ti@jdh.com.co", admin=True)
        self.caller = self.admin
        self.prospects = FakeProspects()
        self.zones = FakeZones()
        self.routes = FakeRoutes()
        self.routing = FakeRouting()
        self.run(
            self.areas.create_area(
                CreateAreaCommand(slug="comercial", name="Comercial"), self.admin
            )
        )

    def run(self, coroutine):
        return asyncio.run(coroutine)

    def member(self, email: str, action: str):
        person = self.directory.sign_in(email)
        role = self.run(self.areas.create_role("comercial", f"Rol {email}", self.admin))
        self.run(
            self.areas.set_role_permissions(
                "comercial", role.id, [("mapa-comercial", action)], self.admin
            )
        )
        self.run(self.areas.add_member("comercial", email, role.id, self.admin))
        return person


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def client(world):
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[get_current_principal] = lambda: world.caller
    v1 = app.state.container.v1_container
    with (
        v1.area_service.override(world.areas),
        v1.user_directory.override(world.directory),
        v1.territory_prospect_repository.override(world.prospects),
        v1.territory_zone_repository.override(world.zones),
        v1.territory_route_repository.override(world.routes),
        v1.territory_routing.override(world.routing),
        TestClient(app) as test_client,
    ):
        yield test_client
    app.dependency_overrides.clear()


def upload(client, apply: bool):
    content = workbook(
        {
            "Belén": [row("BEL-1", "Deli", 6.21, -75.6), row("BEL-2", "Sin punto", latitude=None)],
            "Centro": [row("C-1", "Bar", 6.24, -75.57)],
        }
    )
    return client.post(
        f"{BASE}/prospects/import",
        params={"apply": str(apply).lower()},
        files={"file": ("prospectos.xlsx", content, XLSX)},
    )


def test_outsiders_only_get_the_permissions_flags(client, world) -> None:
    world.caller = world.directory.sign_in("nadie@jdh.com.co")

    assert client.get(f"{BASE}/permissions").json() == {"can_read": False, "can_manage": False}
    for path in ("/prospects/map", "/prospects", "/zones", "/routes", "/prospects/facets"):
        assert client.get(f"{BASE}{path}").status_code == 403, path


def test_a_reader_sees_the_map_but_cannot_load_or_draw(client, world) -> None:
    world.caller = world.admin
    assert upload(client, apply=True).json()["applied"] is True

    world.caller = world.member("vendedor@jdh.com.co", "read")
    assert client.get(f"{BASE}/permissions").json() == {"can_read": True, "can_manage": False}

    collection = client.get(f"{BASE}/prospects/map").json()
    assert collection["type"] == "FeatureCollection" and len(collection["features"]) == 2
    feature = next(f for f in collection["features"] if f["properties"]["external_id"] == "BEL-1")
    assert feature["geometry"] == {"type": "Point", "coordinates": [-75.6, 6.21]}

    unlocated = client.get(f"{BASE}/prospects", params={"located": "false"}).json()
    assert unlocated["total"] == 1 and unlocated["items"][0]["latitude"] is None
    card = client.get(f"{BASE}/prospects/{feature['id']}").json()
    assert card["name"] == "Deli" and card["contact_raw"] == {"phone": None, "email": None}

    assert upload(client, apply=False).status_code == 403
    assert (
        client.post(
            f"{BASE}/zones", json={"name": "Z", "color": "red", "geometry": SQUARE}
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"{BASE}/prospects/{feature['id']}", json={"visit_status": "visited"}
        ).status_code
        == 403
    )


def test_a_manager_loads_draws_and_plans_a_route(client, world) -> None:
    world.caller = world.member("jefe@jdh.com.co", "manage")
    world.directory.sign_in("vendedor@jdh.com.co")

    simulated = upload(client, apply=False).json()
    assert simulated["applied"] is False and simulated["to_create"] == 3
    assert simulated["sheets"][0] == {
        "name": "Belén",
        "rows": 2,
        "with_location": 1,
        "without_location": 1,
    }
    assert upload(client, apply=True).json()["applied"] is True
    loads = client.get(f"{BASE}/prospects/loads").json()
    assert loads[0]["rows_read"] == 3 and loads[0]["loaded_by"] == "jefe@jdh.com.co"

    zone = client.post(
        f"{BASE}/zones", json={"name": "Belén", "color": "green", "geometry": SQUARE}
    )
    assert zone.status_code == 201 and zone.json()["properties"]["color"] == "green"
    assert client.get(f"{BASE}/zones").json()["features"][0]["properties"]["name"] == "Belén"
    bad = client.post(f"{BASE}/zones", json={"name": "X", "color": "teal", "geometry": SQUARE})
    assert bad.status_code == 422

    ids = [f["id"] for f in client.get(f"{BASE}/prospects/map").json()["features"]]
    created = client.post(
        f"{BASE}/routes",
        json={
            "name": "Martes",
            "planned_for": "2026-11-18",
            "assignee_email": "vendedor@jdh.com.co",
            "origin": {"latitude": 6.25, "longitude": -75.58, "label": "Bodega"},
            "prospect_ids": ids,
            "optimize": True,
        },
    )
    assert created.status_code == 201, created.text
    route = created.json()
    assert route["geometry_status"] == "road" and route["stop_count"] == 2
    assert route["assignee"]["email"] == "vendedor@jdh.com.co"
    assert route["origin"]["label"] == "Bodega" and route["geometry"]["type"] == "LineString"
    assert [stop["position"] for stop in route["stops"]] == [1, 2]

    stop = route["stops"][0]
    marked = client.patch(
        f"{BASE}/routes/{route['id']}/stops/{stop['id']}",
        json={"visit_status": "interested", "visit_note": "Pide catálogo"},
    )
    assert marked.json()["visit_status"] == "interested"
    listed = client.get(f"{BASE}/routes").json()
    assert listed["total"] == 1 and listed["items"][0]["visited_count"] == 1

    updated = client.put(f"{BASE}/routes/{route['id']}", json={"origin": None}).json()
    assert updated["origin"] is None and updated["geometry_status"] == "pending"
    computed = client.post(f"{BASE}/routes/{route['id']}/compute", json={"optimize": False})
    assert computed.json()["geometry_status"] == "road"
    assert client.delete(f"{BASE}/routes/{route['id']}").status_code == 204
    assert client.get(f"{BASE}/routes/{route['id']}").status_code == 404


def test_requests_are_validated(client, world) -> None:
    world.caller = world.admin

    assert client.get(f"{BASE}/prospects", params={"visit_status": "maybe"}).status_code == 422
    assert client.get(f"{BASE}/zones", params={"layer": "other"}).status_code == 422
    short = client.post(f"{BASE}/routes", json={"name": "X", "prospect_ids": []})
    assert short.status_code == 422
    not_xlsx = client.post(
        f"{BASE}/prospects/import", files={"file": ("p.csv", b"a,b", "text/csv")}
    )
    assert not_xlsx.status_code == 422 and "Formato" in not_xlsx.json()["detail"]
