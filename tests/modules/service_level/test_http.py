"""Nivel de servicio over HTTP with the real permission gates: who opens which view."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.areas.application.dto import CreateAreaCommand
from app.modules.areas.application.service import AreaService
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.service_level.application.access import ServiceLevelAccess
from app.modules.service_level.application.service import ServiceLevelService
from tests.modules.fakes import FakeAreaRepository, FakeDirectory
from tests.modules.service_level.fakes import NOW, TODAY, FakeRepository, FakeSource, line

REAL_PERMISSION_GATES = True
BASE = "/api/v1/service-level"
PERIOD = {"since": "2026-11-01", "until": "2026-11-26"}


class World:
    def __init__(self) -> None:
        self.directory = FakeDirectory()
        self.areas = AreaService(FakeAreaRepository(), self.directory)
        self.admin = self.directory.sign_in("analista.ti@jdh.com.co", admin=True)
        self.caller = self.admin
        self.repository = FakeRepository(
            (("AGOTADO PROVEEDOR", "PROVEEDOR"), ("ERROR DIGITACION", "COMERCIAL"))
        )
        self.source = FakeSource(
            line("short", order="1", ordered="10", dispatched="6", value="1000"),
            line("full", order="1", value="3000"),
        )
        self.run(
            self.areas.create_area(
                CreateAreaCommand(slug="abastecimiento", name="Abastecimiento"), self.admin
            )
        )

    def run(self, coroutine):
        return asyncio.run(coroutine)

    def member(self, email: str, grants: list[tuple[str, str]]):
        person = self.directory.sign_in(email)
        role = self.run(self.areas.create_role("abastecimiento", f"Rol {email}", self.admin))
        self.run(self.areas.set_role_permissions("abastecimiento", role.id, grants, self.admin))
        self.run(self.areas.add_member("abastecimiento", email, role.id, self.admin))
        return person


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def client(world):
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[get_current_principal] = lambda: world.caller
    v1 = app.state.container.v1_container
    with v1.area_service.override(world.areas), v1.user_directory.override(world.directory):
        real = v1.service_level_service()
        # The real access (the area roles above) over the doubles of the tables and the DWH.
        service = ServiceLevelService(
            world.repository,
            world.source,
            real.access,
            cuts=real.cuts,
            bands=real.bands,
            class_months=2,
            sync_days=45,
            start_date=real.start_date,
            max_range_days=real.max_range_days,
            today=lambda: TODAY,
            clock=lambda: NOW,
        )
        world.run(service.sync())
        with v1.service_level_service.override(service), TestClient(app) as test_client:
            yield test_client
    app.dependency_overrides.clear()


def test_permissions_are_open_and_say_what_each_view_allows(client, world) -> None:
    world.caller = world.member("jefe@jdh.com.co", [("nivel-servicio", "read")])

    body = client.get(f"{BASE}/permissions").json()

    assert body["modules"]["nivel-servicio"] == {"can_read": True, "can_manage": False}
    assert body["modules"]["pendientes-pedidos"] == {"can_read": False, "can_manage": False}
    assert body["can_manage"] is False


def test_the_dashboard_reader_cannot_open_the_pending_lines(client, world) -> None:
    world.caller = world.member("jefe@jdh.com.co", [("nivel-servicio", "read")])

    board = client.get(f"{BASE}/dashboard", params=PERIOD)
    lines = client.get(f"{BASE}/lines", params=PERIOD)
    breakdown = client.get(f"{BASE}/dashboard/breakdown", params={**PERIOD, "dimension": "seller"})

    assert board.status_code == 200, board.text
    kpis = board.json()["kpis"]
    assert (kpis["orders"], kpis["orders_pending"], kpis["ns_orders"]) == (1, 1, "0")
    assert kpis["ns_value"] == "0.9"
    assert board.json()["bands"] == {"green_above": "0.96", "amber_from": "0.90"}
    assert breakdown.status_code == 200 and breakdown.json()["items"][0]["key"] == "v1"
    assert lines.status_code == 403
    assert client.get(f"{BASE}/reasons").status_code == 200


def test_a_reader_of_the_pending_lines_cannot_give_reasons(client, world) -> None:
    world.caller = world.member("lector@jdh.com.co", [("pendientes-pedidos", "read")])
    reason = world.repository.reason("AGOTADO PROVEEDOR")

    listing = client.get(f"{BASE}/lines", params={**PERIOD, "pending": "true"})
    refused = client.put(f"{BASE}/lines/short/reason", json={"reason_id": str(reason.id)})
    created = client.post(f"{BASE}/reasons", json={"name": "OTRO", "responsible": "COMERCIAL"})

    assert listing.status_code == 200, listing.text
    assert [item["line_key"] for item in listing.json()["items"]] == ["short"]
    assert listing.json()["kpis"]["lines"] == 1
    assert refused.status_code == 403 and created.status_code == 403


def test_a_manager_gives_reasons_and_keeps_the_catalog(client, world) -> None:
    world.caller = world.member("analista@jdh.com.co", [("pendientes-pedidos", "manage")])
    reason = world.repository.reason("AGOTADO PROVEEDOR")

    given = client.put(
        f"{BASE}/lines/short/reason", json={"reason_id": str(reason.id), "note": "lunes"}
    )
    bulk = client.put(
        f"{BASE}/lines/reason", json={"line_keys": ["full"], "reason_id": str(reason.id)}
    )
    created = client.post(f"{BASE}/reasons", json={"name": "Sin cupo", "responsible": "cartera"})
    patched = client.patch(f"{BASE}/reasons/{created.json()['id']}", json={"is_active": False})
    by_reason = client.get(f"{BASE}/lines", params={**PERIOD, "reason": str(reason.id)})
    without = client.get(f"{BASE}/lines", params={**PERIOD, "reason": "none"})

    assert given.status_code == 200, given.text
    assert given.json()["reason"]["responsible"] == "PROVEEDOR"
    assert given.json()["note"] == "lunes"
    # The served line takes no reason.
    assert bulk.status_code == 422
    assert created.status_code == 201 and created.json()["name"] == "SIN CUPO"
    assert patched.json()["is_active"] is False
    assert by_reason.json()["total"] == 1 and without.json()["total"] == 1
    assert client.get(f"{BASE}/lines", params={**PERIOD, "reason": "x"}).status_code == 422


def test_the_options_pareto_and_classes_answer(client, world) -> None:
    world.caller = world.member("jefe@jdh.com.co", [("nivel-servicio", "read")])

    options = client.get(f"{BASE}/options", params=PERIOD).json()
    pareto = client.get(f"{BASE}/pareto", params={"kind": "cliente"})
    classes = client.get(f"{BASE}/classes").json()
    recompute = client.post(f"{BASE}/classes/recompute")

    assert options["cos"] == [{"code": "003", "name": "CENTRO"}]
    assert options["responsibles"] == ["COMERCIAL", "PROVEEDOR"]
    assert pareto.status_code == 200 and pareto.json()["kind"] == "cliente"
    assert (classes["window_from"], classes["window_to"]) == ("2026-09-01", "2026-10-31")
    assert classes["cuts"] == {"a": "0.80", "b": "0.95", "c": "0.99"}
    assert recompute.status_code == 403


def test_a_period_too_long_is_a_422(client) -> None:
    response = client.get(
        f"{BASE}/dashboard", params={"since": "2024-01-01", "until": "2026-11-26"}
    )

    assert response.status_code == 422


def test_the_daily_job_is_wired_and_the_service_builds() -> None:
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    v1 = app.state.container.v1_container

    assert "service-level-sync" in v1.job_handlers()
    service = v1.service_level_service()
    assert isinstance(service.access, ServiceLevelAccess)
    assert service.source.view == "pedidos_venta_lineas"
