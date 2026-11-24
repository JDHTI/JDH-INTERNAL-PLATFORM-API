"""Costos over HTTP with the real permission gates: who opens which view."""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.areas.application.dto import CreateAreaCommand
from app.modules.areas.application.service import AreaService
from app.modules.costs.application.access import CostAccess
from app.modules.costs.application.entries import EntryAnalysisService, EntryCache
from app.modules.costs.application.novelties import NoveltyService
from app.modules.identity.presentation.principal import get_current_principal
from tests.modules.costs.fakes import (
    NOW,
    TODAY,
    FakeGuard,
    FakeNoveltyRepository,
    FakeSource,
    FakeStorage,
    line,
)
from tests.modules.fakes import FakeAreaRepository, FakeDirectory

REAL_PERMISSION_GATES = True
BASE = "/api/v1/costs"
PDF = b"%PDF-1.7 nota"


class World:
    def __init__(self) -> None:
        self.directory = FakeDirectory()
        self.areas = AreaService(FakeAreaRepository(), self.directory)
        self.admin = self.directory.sign_in("analista.ti@jdh.com.co", admin=True)
        self.caller = self.admin
        self.repository = FakeNoveltyRepository(
            (("INCREMENTO NO ANUNCIADO", True), ("NEGOCIACION ESPECIAL", False))
        )
        self.source = FakeSource(
            line("ok", price="50000", quotation="50000"),
            line("up", price="52500", quotation="50000", sku="2548"),
        )
        self.storage = FakeStorage()
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
    with (
        v1.area_service.override(world.areas),
        v1.user_directory.override(world.directory),
        v1.cost_entry_source.override(world.source),
        v1.cost_novelty_repository.override(world.repository),
        v1.cost_entry_cache.override(EntryCache()),
    ):
        # The real access (the area roles above); the bucket and the antivirus are doubles.
        novelties = NoveltyService(
            world.repository,
            world.source,
            v1.cost_access(),
            world.storage,
            FakeGuard(),
            threshold=Decimal("0.001"),
            sync_days=45,
            start_date=date(2026, 11, 1),
            today=lambda: TODAY,
            clock=lambda: NOW,
        )
        with v1.cost_novelty_service.override(novelties), TestClient(app) as test_client:
            yield test_client
    app.dependency_overrides.clear()


def test_permissions_are_open_and_say_what_each_view_allows(client, world) -> None:
    world.caller = world.member("comprador@jdh.com.co", [("novedades-compras", "manage")])

    body = client.get(f"{BASE}/permissions").json()

    assert body["modules"]["novedades-compras"] == {"can_read": True, "can_manage": True}
    assert body["modules"]["analisis-entradas"] == {"can_read": False, "can_manage": False}
    assert body["can_set_reasons"] is True


def test_a_buyer_opens_the_novelties_but_not_the_entries(client, world) -> None:
    world.caller = world.member("comprador@jdh.com.co", [("novedades-compras", "manage")])

    assert client.get(f"{BASE}/entries").status_code == 403
    assert client.get(f"{BASE}/novelties").status_code == 200
    assert client.get(f"{BASE}/reasons").status_code == 200


def test_a_reader_sees_the_entries_but_cannot_give_reasons(client, world) -> None:
    world.caller = world.member("lector@jdh.com.co", [("analisis-entradas", "read")])
    reason = world.repository.reason("NEGOCIACION ESPECIAL")

    listing = client.get(f"{BASE}/entries", params={"since": "2026-11-01", "until": "2026-11-23"})
    refused = client.put(f"{BASE}/entries/up/reason", json={"reason_id": str(reason.id)})

    assert listing.status_code == 200 and listing.json()["counts"]["novedad"] == 1
    assert refused.status_code == 403
    assert client.get(f"{BASE}/novelties").status_code == 403


def test_an_analyst_gives_a_reason_and_registers_the_credit_note(client, world) -> None:
    world.caller = world.member(
        "analista@jdh.com.co",
        [("analisis-entradas", "manage"), ("novedades-compras", "manage")],
    )
    reason = world.repository.reason("INCREMENTO NO ANUNCIADO")

    given = client.put(f"{BASE}/entries/up/reason", json={"reason_id": str(reason.id)})
    novelty_id = given.json()["id"]
    entries = client.get(
        f"{BASE}/entries",
        params={
            "since": "2026-11-01",
            "until": "2026-11-23",
            "novelty": "true",
            "sort": "difference_pct",
        },
    ).json()
    noted = client.post(
        f"{BASE}/novelties/{novelty_id}/credit-note",
        data={"number": "NC-17", "amount": "12500"},
        files={"file": ("nc.pdf", PDF, "application/pdf")},
    )
    download = client.get(
        f"{BASE}/novelties/{novelty_id}/credit-note/file", params={"inline": "true"}
    )

    assert given.status_code == 200 and given.json()["status"] == "espera_nota_credito"
    assert Decimal(given.json()["line"]["difference_pct"]) == Decimal("0.05")
    assert entries["items"][0]["novelty"]["reason_name"] == "INCREMENTO NO ANUNCIADO"
    assert entries["threshold"] == "0.001"
    assert noted.status_code == 201, noted.text
    assert (
        noted.json()["status"] == "resuelta" and noted.json()["credit_note"]["amount"] == "12500.00"
    )
    assert download.status_code == 200 and download.content == PDF
    assert download.headers["content-disposition"].startswith("inline;")
    events = client.get(f"{BASE}/novelties/{novelty_id}").json()["events"]
    assert [event["event_type"] for event in events] == ["detectada", "motivo", "nota_credito"]


def test_the_dropdown_options_follow_each_view_gate(client, world) -> None:
    world.caller = world.member("lector@jdh.com.co", [("analisis-entradas", "read")])
    period = {"since": "2026-11-01", "until": "2026-11-23"}

    entries = client.get(f"{BASE}/entries/options", params=period)
    tray = client.get(f"{BASE}/novelties/options")

    assert entries.status_code == 200, entries.text
    assert entries.json()["cos"] == [{"code": "003", "name": "CENTRO"}]
    assert entries.json()["warehouses"] == [{"code": "BTA9", "co": "003"}]
    assert tray.status_code == 403

    world.caller = world.member("comprador@jdh.com.co", [("novedades-compras", "read")])
    tray = client.get(f"{BASE}/novelties/options", params=period)
    # Not taken for a novelty id.
    assert tray.status_code == 200, tray.text
    assert set(tray.json()) == {"cos", "warehouses"}
    assert client.get(f"{BASE}/entries/options").status_code == 403


def test_a_period_too_long_is_a_422(client, world) -> None:
    response = client.get(f"{BASE}/entries", params={"since": "2025-01-01", "until": "2026-11-23"})

    assert response.status_code == 422


def test_the_daily_job_is_wired() -> None:
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    v1 = app.state.container.v1_container
    handlers = v1.job_handlers()

    assert "costs-novelties-sync" in handlers
    # The real service builds: the credit note guard takes only PDF and pictures.
    service = v1.cost_novelty_service()
    assert service.guard.allowed_extensions == {"pdf", "png", "jpg", "jpeg"}
    assert service.guard.max_attachment_bytes == 10 * 1024 * 1024


def test_services_build_from_the_container(client) -> None:
    v1 = client.app.state.container.v1_container
    assert isinstance(v1.cost_access(), CostAccess)
    service = v1.cost_entry_service()
    assert isinstance(service, EntryAnalysisService) and service.threshold == Decimal("0.001")
    novelties = v1.cost_novelty_service()
    assert isinstance(novelties, NoveltyService) and novelties.start_date == date(2026, 11, 1)
