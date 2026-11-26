"""The commercial budget and the rep master over HTTP, with the real permission gates."""

from __future__ import annotations

import asyncio
import io
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.areas.application.dto import CreateAreaCommand
from app.modules.areas.application.service import AreaService
from app.modules.areas.infrastructure.policies import PermissionPolicy
from app.modules.commercial_budget.application.service import BudgetService
from app.modules.commercial_budget.application.vendor_master import VendorMasterService
from app.modules.commercial_budget.infrastructure.budget_file import (
    budget_template,
    read_budget_file,
)
from app.modules.commercial_budget.infrastructure.dwh_platform import DwhPlatformClient
from app.modules.commercial_budget.infrastructure.export_file import board_workbook
from app.modules.commercial_budget.infrastructure.policies import AnyAreaLeaderPolicy
from app.modules.commercial_budget.infrastructure.vendor_template import vendor_master_template
from app.modules.identity.presentation.principal import get_current_principal
from tests.modules.commercial_budget.fakes import (
    FakeRepository,
    FakeWarehouse,
    Scopes,
    rep,
    sold,
)
from tests.modules.commercial_budget.test_vendor_master import History
from tests.modules.fakes import FakeAreaRepository, FakeDirectory

REAL_PERMISSION_GATES = True
BASE = "/api/v1/commercial-budget"
MASTER = "/api/v1/vendor-master"


class World:
    def __init__(self) -> None:
        self.directory = FakeDirectory()
        self.areas = AreaService(FakeAreaRepository(), self.directory)
        self.admin = self.directory.sign_in("analista.ti@jdh.com.co", admin=True)
        self.caller = self.admin
        for slug, name in (("bi", "BI"), ("comercial", "Comercial")):
            asyncio.run(self.areas.create_area(CreateAreaCommand(slug=slug, name=name), self.admin))
        repository = FakeRepository()
        repository.assign("c1", "101", "Arismendy")
        self.service = BudgetService(
            repository,
            FakeWarehouse({"2026-09": [sold("c1", "1000", "800")]}, [rep("101", "Arismendy")]),
            PermissionPolicy(self.areas, None, "presupuesto-comercial"),
            AnyAreaLeaderPolicy(self.areas, ["bi", "comercial"]),
            Scopes(),
            start_period="2026-09",
            backfill_months=0,
            writer=board_workbook,
            template_writer=budget_template,
            reader=read_budget_file,
            today=lambda: date(2026, 10, 5),
        )
        self.uploads = History()
        self.vendor_master = VendorMasterService(
            DwhPlatformClient(
                "http://dwh-platform",
                "",
                5,
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(200, json={"vendedores_procesados": 1})
                ),
            ),
            self.uploads,
            PermissionPolicy(self.areas, None, "maestro-vendedores"),
            vendor_master_template,
        )

    def member(self, area: str, email: str, grants: list[tuple[str, str]]):
        person = self.directory.sign_in(email)
        role = asyncio.run(self.areas.create_role(area, f"Rol {email}", self.admin))
        asyncio.run(self.areas.set_role_permissions(area, role.id, grants, self.admin))
        asyncio.run(self.areas.add_member(area, email, role.id, self.admin))
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
        v1.commercial_budget_service.override(world.service),
        v1.vendor_master_service.override(world.vendor_master),
        TestClient(app) as test_client,
    ):
        yield test_client
    app.dependency_overrides.clear()


def test_permissions_are_open_and_the_rest_is_gated(client, world) -> None:
    world.caller = world.directory.sign_in("nadie@jdh.com.co")

    assert client.get(f"{BASE}/permissions").json() == {
        "can_read": False,
        "can_manage": False,
        "can_close": False,
        "scope": "none",
    }
    assert client.get(f"{BASE}/periods").status_code == 403
    assert client.get(f"{MASTER}/status").json()["can_read"] is False
    assert client.get(f"{MASTER}/template").status_code == 403


def test_comercial_fills_the_budget_and_reads_the_board(client, world) -> None:
    world.caller = world.member(
        "comercial", "analista@jdh.com.co", [("presupuesto-comercial", "manage")]
    )
    client.post(f"{BASE}/sync")

    saved = client.put(
        f"{BASE}/2026-09/budgets",
        json={"items": [{"salesperson_code": "101", "budget_sales": 1250, "budget_margin": 0.2}]},
    )
    board = client.get(f"{BASE}/2026-09").json()
    exported = client.get(f"{BASE}/2026-09/export")

    assert saved.status_code == 200 and saved.json()[0]["version"] == 1
    row = board["rows"][0]
    assert row["salesperson_code"] == "101" and float(row["current"]["net"]) == 1000
    assert float(row["derived"]["compliance"]) == 0.8
    assert board["window"] == {
        "current": "2026-09",
        "previous": ["2026-06", "2026-07", "2026-08"],
        "last_year": "2025-09",
    }
    assert board["team_groups"][0]["regional"] == "ANTIOQUIA"
    assert float(board["team_groups"][0]["current"]["net"]) == 1000
    assert exported.status_code == 200
    assert client.post(f"{BASE}/2026-09/close").status_code == 403


def test_a_leader_closes_and_a_closed_month_refuses_changes(client, world) -> None:
    world.caller = world.admin
    client.post(f"{BASE}/sync")

    closed = client.post(f"{BASE}/2026-09/close")
    refused = client.put(
        f"{BASE}/2026-09/budgets", json={"items": [{"salesperson_code": "101", "budget_sales": 1}]}
    )
    reopened = client.post(f"{BASE}/2026-09/reopen")

    assert closed.status_code == 200 and closed.json()["status"] == "cerrado"
    assert refused.status_code == 409
    assert reopened.json()["status"] == "abierto"


def test_the_budget_workbook_simulates_then_applies(client, world) -> None:
    world.caller = world.admin
    client.post(f"{BASE}/sync")
    workbook = Workbook()
    workbook.active.append(["CÓDIGO VENDEDOR", "PPTO VENTA", "PPTO RECAUDO"])
    workbook.active.append(["101", 900, 950])
    output = io.BytesIO()
    workbook.save(output)
    files = {"file": ("ppto.xlsx", output.getvalue(), "application/octet-stream")}

    simulated = client.post(f"{BASE}/2026-09/budget-upload", files=files).json()
    applied = client.post(f"{BASE}/2026-09/budget-upload?apply=true", files=files).json()
    changes = client.get(f"{BASE}/2026-09/changes").json()

    assert simulated["applied"] is False and simulated["changed"] == 1
    assert applied["applied"] is True
    assert changes[0]["source"] == "archivo" and changes[0]["salesperson_code"] == "101"


def test_bi_uploads_the_rep_master(client, world) -> None:
    world.caller = world.member("bi", "maestro@jdh.com.co", [("maestro-vendedores", "manage")])

    status = client.get(f"{MASTER}/status").json()
    template = client.get(f"{MASTER}/template")
    uploaded = client.post(
        f"{MASTER}/upload", files={"file": ("Maestro.xlsx", b"PK..", "application/octet-stream")}
    )

    assert status == {"configured": True, "can_read": True, "can_manage": True}
    assert template.status_code == 200 and template.content[:2] == b"PK"
    assert uploaded.json() == {"vendedores_procesados": 1}
    assert world.uploads.items[0]["actor"] == "maestro@jdh.com.co"
