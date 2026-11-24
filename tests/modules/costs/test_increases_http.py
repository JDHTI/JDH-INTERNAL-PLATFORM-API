"""Supplier increases over HTTP with the real permission gates."""

from __future__ import annotations

import asyncio
import io
from datetime import datetime
from urllib.parse import unquote

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.areas.application.dto import CreateAreaCommand
from app.modules.areas.application.service import AreaService
from app.modules.costs.application.increases import IncreaseService
from app.modules.costs.infrastructure.increase_file import (
    COLUMNS,
    FIRST_ROW,
    SHEET,
    IncreaseWorkbookReader,
    template_workbook,
    workbook_name,
)
from app.modules.identity.presentation.principal import get_current_principal
from tests.modules.costs.fakes import NOW, TODAY, FakeStorage
from tests.modules.costs.increase_fakes import FakeIncreaseRepository, FakeItems, FakeLedger, item
from tests.modules.fakes import FakeAreaRepository, FakeDirectory

REAL_PERMISSION_GATES = True
BASE = "/api/v1/costs/increase-requests"
COLUMN = {column.header: position for position, column in enumerate(COLUMNS, start=1)}


class Reasons:
    async def __call__(self):
        return []


class World:
    def __init__(self) -> None:
        self.directory = FakeDirectory()
        self.areas = AreaService(FakeAreaRepository(), self.directory)
        self.admin = self.directory.sign_in("analista.ti@jdh.com.co", admin=True)
        self.caller = self.admin
        self.ledger = FakeLedger()
        asyncio.run(
            self.areas.create_area(
                CreateAreaCommand(slug="abastecimiento", name="Abastecimiento"), self.admin
            )
        )

    def member(self, email: str, action: str):
        person = self.directory.sign_in(email)
        role = asyncio.run(self.areas.create_role("abastecimiento", f"Rol {email}", self.admin))
        asyncio.run(
            self.areas.set_role_permissions(
                "abastecimiento", role.id, [("alzas-proveedor", action)], self.admin
            )
        )
        asyncio.run(self.areas.add_member("abastecimiento", email, role.id, self.admin))
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
        increases = IncreaseService(
            FakeIncreaseRepository(),
            FakeItems(item("10042", "22039.88"), item("9894", "56962.04")),
            world.ledger,
            v1.cost_access(),
            FakeStorage(),
            IncreaseWorkbookReader(),
            template_workbook,
            workbook_name,
            Reasons(),
            max_upload_bytes=1024 * 1024,
            today=lambda: TODAY,
            clock=lambda: NOW,
        )
        with v1.cost_increase_service.override(increases), TestClient(app) as test_client:
            yield test_client
    app.dependency_overrides.clear()


def test_a_reader_downloads_the_maestra_but_cannot_load_it(client, world) -> None:
    world.caller = world.member("lector@jdh.com.co", "read")

    response = client.get(f"{BASE}/template", params={"supplier_code": "50"})

    assert response.status_code == 200
    disposition = unquote(response.headers["content-disposition"])
    assert disposition.endswith("MAESTRA 50 - ASCENDER S.A. 23-11-2026.xlsx")
    assert (
        client.get(f"{BASE}/items", params={"supplier_code": "50", "q": "98"}).json()[0]["sku"]
        == "9894"
    )
    refused = client.post(
        f"{BASE}/upload",
        params={"supplier_code": "50"},
        files={"file": ("m.xlsx", response.content, "application/octet-stream")},
    )
    assert refused.status_code == 403


def test_someone_outside_the_module_gets_403(client, world) -> None:
    world.caller = world.directory.sign_in("otro@jdh.com.co")

    assert client.get(BASE).status_code == 403


def test_the_whole_flow_from_workbook_to_history(client, world) -> None:
    world.caller = world.member("analista@jdh.com.co", "manage")
    template = client.get(f"{BASE}/template", params={"supplier_code": "50"}).content
    book = openpyxl.load_workbook(io.BytesIO(template))
    sheet = book[SHEET]
    sheet.cell(FIRST_ROW, COLUMN["Costo Nuevo sin Impuesto Saludable"], 23000)
    sheet.cell(FIRST_ROW, COLUMN["Fecha de Incremento"], datetime(2026, 12, 1))
    sheet.cell(FIRST_ROW, COLUMN["Regional"], "CARTAGENA")
    sheet.cell(FIRST_ROW, COLUMN["Precio Venta Público sin IVA (PVP)"], 29990)
    buffer = io.BytesIO()
    book.save(buffer)
    files = {"file": ("maestra.xlsx", buffer.getvalue(), "application/octet-stream")}

    simulated = client.post(f"{BASE}/upload", params={"supplier_code": "50"}, files=files)
    loaded = client.post(
        f"{BASE}/upload",
        params={"supplier_code": "50", "apply": "true"},
        files=files,
        data={"note": "Carta"},
    )
    request_id = loaded.json()["request_id"]
    listing = client.get(BASE, params={"status": "en_revision"}).json()
    approved = client.post(f"{BASE}/{request_id}/approve", json={"note": None})

    assert simulated.status_code == 200 and simulated.json()["applied"] is False
    line = simulated.json()["lines"][0]
    assert (line["regional"], line["regionals"], line["public_price"]) == (
        "CARTAGENA",
        ["CARTAGENA"],
        "29990.00",
    )
    assert loaded.json()["applied"] is True and listing["total"] == 1
    assert listing["items"][0]["has_file"] is True and listing["items"][0]["note"] == "Carta"
    assert approved.status_code == 200 and approved.json()["status"] == "aprobada"
    assert [(entry.sku, entry.regional) for entry in world.ledger.entries] == [
        ("10042", "CARTAGENA")
    ]
    assert client.get(f"{BASE}/{request_id}/file").content == buffer.getvalue()


def test_a_manual_request_validates_its_body(client, world) -> None:
    world.caller = world.member("analista@jdh.com.co", "manage")

    bad = client.post(BASE, json={"supplier_code": "50", "lines": []})
    wrong_regional = client.post(
        BASE,
        json={
            "supplier_code": "50",
            "lines": [
                {
                    "sku": "10042",
                    "new_cost": "23000",
                    "increased_on": "2026-12-01",
                    "regional": "COSTA",
                }
            ],
        },
    )
    created = client.post(
        BASE,
        json={
            "supplier_code": "50",
            "lines": [{"sku": "10042", "new_cost": "23000", "increased_on": "2026-12-01"}],
        },
    )

    assert bad.status_code == 422 and wrong_regional.status_code == 422
    assert created.status_code == 201 and created.json()["source"] == "manual"
    line = created.json()["lines"][0]
    patched = client.patch(
        f"{BASE}/{created.json()['id']}/lines/{line['id']}", json={"accepted": False}
    )
    assert patched.json()["accepted_count"] == 0
    rejected = client.post(f"{BASE}/{created.json()['id']}/reject", json={"note": "Sin carta"})
    assert rejected.json()["status"] == "rechazada"


def test_the_real_service_builds_from_the_container() -> None:
    v1 = create_app(Settings(ENVIRONMENT=Environment.TEST)).state.container.v1_container

    service = v1.cost_increase_service()

    assert isinstance(service, IncreaseService)
    assert service.max_upload_bytes == 10 * 1024 * 1024
