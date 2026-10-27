"""HTTP contract of the application monitoring routes and the general board."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.app_monitoring.application.board import (
    MonitoringBoardService,
    warehouse_status,
)
from app.modules.app_monitoring.application.service import AppMonitoringService
from app.modules.app_monitoring.domain.entities import CheckStatus, Target
from app.modules.dwh_monitoring.application.service import DwhMonitoringService
from app.modules.dwh_monitoring.domain.entities import CycleStatus, LoadCycle
from app.modules.identity.presentation.principal import get_current_principal
from tests.modules.app_monitoring.conftest import NOW, TI_MEMBER, FakeAccess
from tests.modules.dwh_monitoring.conftest import OUTSIDER
from tests.modules.dwh_monitoring.test_service import FakeReader, card

BASE = "/api/v1/app-monitoring"


class AllowAll:
    async def allow_sliding_window(self, bucket, limit, window):
        return SimpleNamespace(allowed=True, count=1, retry_after=0)

    async def close(self):
        return None


def dwh_reader(status=CycleStatus.SUCCESS) -> FakeReader:
    reader = FakeReader()
    reader.cards = [card("reporte_ventas")]
    reader.cycle = LoadCycle(id=uuid4(), started_at=NOW, finished_at=NOW, status=status)
    return reader


def build(apps, prober, principal=TI_MEMBER, reader=None):
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[get_current_principal] = lambda: principal
    service = AppMonitoringService(apps, prober, FakeAccess(), clock=lambda: NOW)
    dwh = DwhMonitoringService(reader or dwh_reader(), FakeAccess(), clock=lambda: NOW)
    board = MonitoringBoardService(service, dwh, clock=lambda: NOW)
    return app, service, board


@pytest.fixture
def client(apps, prober):
    app, service, board = build(apps, prober)
    v1 = app.state.container.v1_container
    with (
        app.state.container.redis.override(AllowAll()),
        v1.app_monitoring_service.override(service),
        v1.monitoring_board_service.override(board),
        TestClient(app) as test_client,
    ):
        yield test_client
    app.dependency_overrides.clear()


def test_register_check_and_read_an_application(client) -> None:
    created = client.post(
        f"{BASE}/applications",
        json={"nombre": "Intranet", "url_base": "https://intranet.jdh.local"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["slug"] == "intranet" and body["ruta_health"] == "/api/health"
    assert body["estado"] is None and body["api"]["ultimo"] is None

    checked = client.post(f"{BASE}/applications/{body['id']}/check").json()
    assert checked["estado"] == "up"
    assert checked["api"]["ultimo"]["objetivo"] == "api"
    assert checked["api"]["url"] == "https://intranet.jdh.local/api/health"
    assert checked["ui"]["disponibilidad_24h"] == 100

    history = client.get(f"{BASE}/applications/{body['id']}/checks", params={"objetivo": "ui"})
    assert history.status_code == 200 and history.json()["total"] == 1

    listed = client.get(f"{BASE}/applications").json()["items"]
    assert [item["slug"] for item in listed] == ["intranet"]


def test_bad_input_is_rejected(client) -> None:
    bad_url = client.post(
        f"{BASE}/applications", json={"nombre": "X", "url_base": "https://x.jdh.local/api"}
    )
    missing = client.post(f"{BASE}/applications", json={"nombre": "X"})
    timeout = client.post(
        f"{BASE}/applications",
        json={"nombre": "X", "url_base": "https://x.jdh.local", "timeout_s": 0},
    )

    assert bad_url.status_code == 422 and "ruta_health" in bad_url.text
    assert missing.status_code == 422 and timeout.status_code == 422


def test_patch_and_delete(client) -> None:
    created = client.post(
        f"{BASE}/applications", json={"nombre": "Tienda", "url_base": "https://tienda.jdh.local"}
    ).json()

    patched = client.patch(f"{BASE}/applications/{created['id']}", json={"verificar_tls": False})
    assert patched.status_code == 200 and patched.json()["verificar_tls"] is False
    assert patched.json()["nombre"] == "Tienda"

    assert client.delete(f"{BASE}/applications/{created['id']}").status_code == 204
    assert client.get(f"{BASE}/applications/{created['id']}").status_code == 404


def test_manual_check_of_everything_needs_the_loop_on(client) -> None:
    assert client.post(f"{BASE}/check").status_code == 409


def test_the_board_combines_applications_and_the_warehouse(client, prober) -> None:
    for name, url in (
        ("Intranet", "https://intranet.jdh.local"),
        ("Tienda", "https://tienda.jdh.local"),
    ):
        created = client.post(f"{BASE}/applications", json={"nombre": name, "url_base": url})
        client.post(f"{BASE}/applications/{created.json()['id']}/check")
    prober.answers[("tienda", Target.UI)] = CheckStatus.DOWN
    tienda = client.get(f"{BASE}/applications").json()["items"][1]
    client.post(f"{BASE}/applications/{tienda['id']}/check")

    board = client.get("/api/v1/monitoring/board")

    assert board.status_code == 200
    body = board.json()
    assert body["estado_general"] == "down"
    assert body["aplicativos"]["caidos"] == 1 and body["aplicativos"]["arriba"] == 1
    assert body["datawarehouse"]["estado"] == "up"
    assert body["datawarehouse"]["ultima_carga"]["estado"] == "success"
    assert body["datawarehouse"]["secuencias_activas"] == 1


def test_outsiders_are_kept_out(apps, prober) -> None:
    app, service, board = build(apps, prober, principal=OUTSIDER)
    v1 = app.state.container.v1_container
    with (
        app.state.container.redis.override(AllowAll()),
        v1.app_monitoring_service.override(service),
        v1.monitoring_board_service.override(board),
        TestClient(app) as test_client,
    ):
        assert test_client.get(f"{BASE}/applications").status_code == 403
        assert test_client.get("/api/v1/monitoring/board").status_code == 403


def _dwh(**load) -> dict:
    base = {"habilitada": True, "intervalo_s": 600, "ultima": None, "ultima_exitosa": None}
    return {
        "carga": {**base, **load},
        "frescura": {"atrasadas": 0, "nunca_extraidas": 0},
        "datos_listos_hoy": [{"ultima_estado": "success"}],
    }


def test_warehouse_traffic_light() -> None:
    ok = LoadCycle(id=uuid4(), started_at=NOW, status=CycleStatus.SUCCESS)
    failed = LoadCycle(id=uuid4(), started_at=NOW, status=CycleStatus.FAILED)
    partial = LoadCycle(id=uuid4(), started_at=NOW, status=CycleStatus.PARTIAL)

    assert warehouse_status(_dwh(), NOW) is None
    assert warehouse_status(_dwh(ultima=ok, ultima_exitosa=NOW), NOW) is CheckStatus.UP
    assert warehouse_status(_dwh(ultima=failed, ultima_exitosa=NOW), NOW) is CheckStatus.DOWN
    stale = _dwh(ultima=ok, ultima_exitosa=NOW - timedelta(hours=1))
    assert warehouse_status(stale, NOW) is CheckStatus.DOWN
    assert warehouse_status(_dwh(ultima=partial, ultima_exitosa=NOW), NOW) is CheckStatus.DEGRADED
    late = {**_dwh(ultima=ok, ultima_exitosa=NOW), "frescura": {"atrasadas": 2}}
    assert warehouse_status(late, NOW) is CheckStatus.DEGRADED
