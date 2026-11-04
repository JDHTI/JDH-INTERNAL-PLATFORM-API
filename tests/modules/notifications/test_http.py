"""HTTP contract of the notifications: the bell, push and Strapi's ingestion."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.identity.presentation.principal import get_current_principal
from tests.modules.fakes import principal
from tests.modules.notifications.conftest import World

BASE = "/api/v1/notifications"
SECRET = "s" * 40
ANA = principal("ana@jdh.com.co")


@pytest.fixture
def client():
    world = World()
    world.directory.employee(user_id=ANA.user_id)
    app = create_app(
        Settings(_env_file=None, ENVIRONMENT=Environment.TEST, NOTIFICATIONS_INGEST_SECRET=SECRET)
    )
    app.dependency_overrides[get_current_principal] = lambda: ANA
    with (
        app.state.container.v1_container.notification_service.override(world.service),
        TestClient(app) as test_client,
    ):
        yield test_client, world
    app.dependency_overrides.clear()


def _publication(**fields) -> dict:
    body = {
        "fuente": "EVENTO",
        "fuenteId": "evt-1",
        "slug": "feria",
        "titulo": "Feria de proveedores",
        "resumen": "El jueves",
        "idempotencyKey": "evt-1:2026-11-04T10:00:00Z",
        "audiencia": {"tipo": "TODOS"},
    }
    body.update(fields)
    return body


def test_ingestion_needs_the_shared_secret(client) -> None:
    test_client, _ = client

    missing = test_client.post(f"{BASE}/ingest", json=_publication())
    wrong = test_client.post(
        f"{BASE}/ingest", json=_publication(), headers={"X-Jdh-Ingest-Secret": "x" * 40}
    )
    audiences = test_client.get(f"{BASE}/ingest/audiences")

    assert missing.status_code == wrong.status_code == audiences.status_code == 401


def test_ingestion_without_a_secret_configured_is_off() -> None:
    app = create_app(Settings(_env_file=None, ENVIRONMENT=Environment.TEST))
    with TestClient(app) as test_client:
        response = test_client.post(
            f"{BASE}/ingest", json=_publication(), headers={"X-Jdh-Ingest-Secret": SECRET}
        )
    assert response.status_code == 503


def test_a_publication_reaches_the_bell(client) -> None:
    test_client, _ = client
    headers = {"X-Jdh-Ingest-Secret": SECRET}

    created = test_client.post(f"{BASE}/ingest", json=_publication(), headers=headers)
    retried = test_client.post(f"{BASE}/ingest", json=_publication(), headers=headers)

    assert created.status_code == 200
    assert created.json()["estado"] == "ENVIADA" and created.json()["destinatarios"] == 1
    assert retried.json()["duplicada"] is True
    invalid = test_client.post(
        f"{BASE}/ingest",
        json=_publication(fuenteId="x", audiencia={"tipo": "SEDE"}),
        headers=headers,
    )
    assert invalid.status_code == 422

    feed = test_client.get(BASE, params={"page": 1, "pageSize": 5}).json()
    assert feed["total"] == 1 and feed["no_leidas"] == 1 and feed["pageSize"] == 5
    item = feed["items"][0]
    assert item["titulo"] == "Feria de proveedores" and item["href"] == "/eventos/feria"
    assert item["fuente"] == "EVENTO" and item["leida"] is False

    assert test_client.patch(f"{BASE}/{item['id']}/read").status_code == 204
    assert test_client.get(f"{BASE}/count").json() == {"no_leidas": 0}
    assert test_client.patch(f"{BASE}/read").status_code == 204
    catalog = test_client.get(f"{BASE}/ingest/audiences", headers=headers).json()
    assert catalog["areas"] == [{"nombre": "BI", "empleados": 3}]


def test_push_subscriptions(client) -> None:
    test_client, world = client

    assert test_client.get(f"{BASE}/push/public-key").json() == {"clave": "public-key"}
    subscribed = test_client.post(
        f"{BASE}/push/subscriptions",
        json={"endpoint": "https://push.test/a", "p256dh": "k", "auth": "a"},
    )
    assert subscribed.status_code == 204 and "https://push.test/a" in world.repository.subs
    removed = test_client.delete(
        f"{BASE}/push/subscriptions", params={"endpoint": "https://push.test/a"}
    )
    assert removed.status_code == 204 and world.repository.subs == {}
