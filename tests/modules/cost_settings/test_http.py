"""HTTP contract of ``/api/v1/cost-settings``."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.identity.presentation.principal import get_current_principal
from tests.modules.cost_settings.conftest import COST_MANAGER, COST_READER
from tests.modules.supplier_supports.conftest import person

BASE = "/api/v1/cost-settings"


class AllowAll:
    async def allow_sliding_window(self, bucket, limit, window):
        return SimpleNamespace(allowed=True, count=1, retry_after=0)

    async def close(self):
        return None


@pytest.fixture
def client(settings):
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[get_current_principal] = lambda: person(COST_MANAGER)
    container = app.state.container
    with (
        container.redis.override(AllowAll()),
        container.v1_container.cost_settings_service.override(settings),
        TestClient(app) as test_client,
    ):
        yield test_client
    app.dependency_overrides.clear()


def test_the_cost_settings_contract(client) -> None:
    assert client.get(f"{BASE}/permissions").json() == {"can_read": True, "can_manage": True}

    overview = client.get(BASE)
    assert overview.status_code == 200
    body = overview.json()
    threshold = next(item for item in body["parameters"] if item["key"] == "umbral_negociacion")
    assert threshold["unit"] == "margen" and threshold["is_default"] is True
    assert {item["family"] for item in body["parameters"] if item["unit"] == "factor"} == {
        "generales",
        "mayoristas",
        "market",
    }
    # Salsamentarias is no family since 2026-11-25: its lists are Mayoristas.
    assert (
        client.put(
            f"{BASE}/parameters", json={"values": {"factor_costo_salsamentarias": 1.004}}
        ).status_code
        == 422
    )

    changed = client.put(
        f"{BASE}/parameters", json={"values": {"umbral_negociacion": 0.015}, "note": "BI"}
    )
    assert changed.status_code == 200
    assert next(item for item in changed.json() if item["key"] == "umbral_negociacion")[
        "value"
    ] in ("0.015", "0.015000")
    assert client.put(f"{BASE}/parameters", json={"values": {"x": 1}}).status_code == 422

    created = client.post(
        f"{BASE}/branch-rules",
        json={
            "supplier_nit": "860000006",
            "supplier_name": "TEAM FOODS COLOMBIA S.A.",
            "center_branches": {"001": "002"},
            "note": "Antioquia",
        },
    )
    assert created.status_code == 201
    rule = created.json()
    assert rule["general_branch"] == "001" and rule["center_branches"] == {"001": "002"}
    again = client.post(
        f"{BASE}/branch-rules",
        json={"supplier_nit": "860000006", "center_branches": {"003": "002"}},
    )
    assert again.status_code == 409

    off = client.patch(f"{BASE}/branch-rules/{rule['id']}", json={"is_active": False})
    assert off.status_code == 200 and off.json()["is_active"] is False
    history = client.get(f"{BASE}/changes").json()
    assert [item["entity"] for item in history[:3]] == [
        "regla_sucursal",
        "regla_sucursal",
        "parametro",
    ]
    assert client.delete(f"{BASE}/branch-rules/{rule['id']}").status_code == 204
    assert client.get(BASE).json()["branch_rules"] == []


def test_a_reader_cannot_change(client) -> None:
    client.app.dependency_overrides[get_current_principal] = lambda: person(COST_READER)
    assert client.get(f"{BASE}/permissions").json() == {"can_read": True, "can_manage": False}
    denied = client.put(f"{BASE}/parameters", json={"values": {"umbral_negociacion": 0.02}})
    assert denied.status_code == 403
