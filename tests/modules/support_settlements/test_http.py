"""The settlement over HTTP with the real permission gates."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.areas.application.dto import CreateAreaCommand
from app.modules.areas.application.service import AreaService
from app.modules.areas.infrastructure.policies import AreaLeaderPolicy, PermissionPolicy
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.support_settlements.application.service import SettlementService
from app.modules.support_settlements.infrastructure.export_file import settlement_workbook
from tests.modules.fakes import FakeAreaRepository, FakeDirectory
from tests.modules.support_settlements.fakes import (
    FakeMasters,
    FakeRepository,
    FakeSales,
    customer,
    product,
    quote,
    sale,
    support,
)

REAL_PERMISSION_GATES = True
BASE = "/api/v1/support-settlements"


class World:
    def __init__(self) -> None:
        self.directory = FakeDirectory()
        self.areas = AreaService(FakeAreaRepository(), self.directory)
        self.admin = self.directory.sign_in("analista.ti@jdh.com.co", admin=True)
        self.caller = self.admin
        asyncio.run(self.areas.create_area(CreateAreaCommand(slug="bi", name="BI"), self.admin))
        self.service = SettlementService(
            FakeRepository(),
            FakeMasters([support()], [product()], [customer()]),
            FakeSales([sale(sale_date=date(2026, 10, 15))], {"18369": [quote("9142.67")]}),
            PermissionPolicy(self.areas, "bi", "liquidacion-apoyos"),
            AreaLeaderPolicy(self.areas, "bi"),
            start_period="2026-10",
            writer=settlement_workbook,
            today=lambda: date(2026, 11, 24),
        )

    def member(self, email: str, grants: list[tuple[str, str]]):
        person = self.directory.sign_in(email)
        role = asyncio.run(self.areas.create_role("bi", f"Rol {email}", self.admin))
        asyncio.run(self.areas.set_role_permissions("bi", role.id, grants, self.admin))
        asyncio.run(self.areas.add_member("bi", email, role.id, self.admin))
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
        v1.support_settlement_service.override(world.service),
        TestClient(app) as test_client,
    ):
        yield test_client
    app.dependency_overrides.clear()


def test_permissions_are_open(client, world) -> None:
    world.caller = world.directory.sign_in("nadie@jdh.com.co")

    body = client.get(f"{BASE}/permissions").json()

    assert body == {"can_read": False, "can_manage": False, "can_reopen": False}
    assert client.get(f"{BASE}/periods").status_code == 403


def test_a_reader_reads_but_does_not_calculate(client, world) -> None:
    world.caller = world.member("lector@jdh.com.co", [("liquidacion-apoyos", "read")])

    assert client.get(f"{BASE}/periods").status_code == 200
    assert client.post(f"{BASE}/2026-10/recalculate").status_code == 403


def test_a_manager_calculates_reads_downloads_and_closes(client, world) -> None:
    world.caller = world.member("analista@jdh.com.co", [("liquidacion-apoyos", "manage")])

    calculated = client.post(f"{BASE}/2026-10/recalculate")
    summary = client.get(f"{BASE}/2026-10").json()
    references = client.get(f"{BASE}/2026-10/references", params={"supplier_code": "233"}).json()
    lines = client.get(f"{BASE}/2026-10/lines").json()
    workbook = client.get(f"{BASE}/2026-10/export", params={"supplier_code": "233"})
    closed = client.post(f"{BASE}/2026-10/close")

    assert calculated.status_code == 200 and calculated.json()["status"] == "borrador"
    assert summary["suppliers"][0]["supplier_code"] == "233"
    assert summary["settlement"]["recomposed_margin"] is not None
    assert references["references"][0]["sku"] == "18369"
    item = lines["items"][0]["items"][0]
    assert lines["total"] == 1 and item["status"] == "liquidado"
    assert item["status_label"] == "Liquidado"
    assert (
        workbook.status_code == 200 and "proveedor-233" in workbook.headers["content-disposition"]
    )
    assert closed.json()["status"] == "cerrado"
    assert client.post(f"{BASE}/2026-10/recalculate").status_code == 409
    assert client.post(f"{BASE}/2026-10/reopen").status_code == 403


def test_both_cost_bases_are_stored_and_closed_on_their_own(client, world) -> None:
    world.caller = world.member("analista@jdh.com.co", [("liquidacion-apoyos", "manage")])
    inventory = {"cost_basis": "inventario"}

    client.post(f"{BASE}/2026-10/recalculate")
    by_quote = client.get(f"{BASE}/2026-10").json()["settlement"]
    by_inventory = client.get(f"{BASE}/2026-10", params=inventory).json()["settlement"]
    lines = client.get(f"{BASE}/2026-10/lines", params=inventory).json()
    workbook = client.get(f"{BASE}/2026-10/export", params=inventory)
    client.post(f"{BASE}/2026-10/close")
    periods = client.get(f"{BASE}/periods", params=inventory).json()

    assert (by_quote["cost_basis"], by_inventory["cost_basis"]) == ("cotizacion", "inventario")
    assert by_inventory["cost_basis_label"] == "Costo inventario (facturado)"
    # The same sale, settled with the invoice's cost (443355 / 45) instead of the quotation.
    assert by_quote["amount"] != by_inventory["amount"]
    assert lines["items"][0]["cost_source"] == "factura"
    assert "2026-10-inventario" in workbook.headers["content-disposition"]
    october = next(item for item in periods if item["period"] == "2026-10")
    assert october["settlement"]["status"] == "borrador"
    assert {item["cost_basis"]: item["status"] for item in october["settlements"]} == {
        "cotizacion": "cerrado",
        "inventario": "borrador",
    }
    assert client.post(f"{BASE}/2026-10/recalculate", params=inventory).status_code == 200
    assert client.get(f"{BASE}/2026-10", params={"cost_basis": "otro"}).status_code == 422


def test_a_bad_period_is_422(client, world) -> None:
    assert client.get(f"{BASE}/2026-13").status_code == 422
    assert client.get(f"{BASE}/2026-10/breakdown", params={"by": "otro"}).status_code == 422
