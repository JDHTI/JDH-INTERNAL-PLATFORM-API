"""HTTP contract of the retained orders validator routes."""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.identity.presentation.principal import get_current_principal
from fastapi.testclient import TestClient

from tests.modules.order_validation.conftest import MANAGER, agreement
from tests.modules.order_validation.test_service import report
from tests.modules.supplier_supports.conftest import person

BASE = "/api/v1/order-validations"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class AllowAll:
    async def allow_sliding_window(self, bucket, limit, window):
        return SimpleNamespace(allowed=True, count=1, retry_after=0)

    async def close(self):
        return None


@pytest.fixture
def client(service, source):
    source.items.append(
        agreement(supplier_value=Decimal("0.05"), target_margin_pct=Decimal("0.14"))
    )
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[get_current_principal] = lambda: person(MANAGER)
    container = app.state.container
    with (
        container.redis.override(AllowAll()),
        container.v1_container.order_validation_service.override(service),
        TestClient(app) as test_client,
    ):
        yield test_client
    app.dependency_overrides.clear()


def upload(client, content: bytes = b"", name: str = "Pedidos Retenidos.xlsx"):
    return client.post(BASE, files={"file": (name, content or report(), XLSX)})


def test_upload_then_read_orders_with_their_lines(client) -> None:
    created = upload(client)

    assert created.status_code == 201
    batch = created.json()
    assert batch["order_count"] == 2 and batch["ready_order_count"] == 1
    assert batch["pending_order_count"] == 1 and batch["pending_line_count"] == 1

    orders = client.get(f"{BASE}/{batch['id']}/orders").json()
    assert orders["total"] == 2 and orders["counts"]["listo_para_liberar"] == 1
    first = orders["items"][0]
    assert first["document"] == "PV-1" and first["status_label"] == "Listo para liberación"
    assert first["customer_nit"] == "900315138" and first["channel"] == "HOTELES Y CLUBES"
    line = first["lines"][0]
    assert line["reason"] == "cumple_con_apoyo"
    assert line["reason_label"] == "Cumple con el apoyo pactado"
    assert Decimal(line["effective_margin_pct"]) == Decimal("0.145")
    assert line["checks"][0]["outcome"] == "cumple" and line["support_id"]

    pending = client.get(f"{BASE}/{batch['id']}/orders", params={"status": "pendiente_validacion"})
    assert [item["document"] for item in pending.json()["items"]] == ["PV-2"]
    assert client.get(f"{BASE}/{batch['id']}/orders", params={"status": "x"}).status_code == 422


def test_list_get_and_revalidate(client) -> None:
    batch = upload(client).json()

    listing = client.get(BASE).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == batch["id"]
    assert client.get(f"{BASE}/{batch['id']}").json()["file_name"] == "Pedidos Retenidos.xlsx"
    again = client.post(f"{BASE}/{batch['id']}/revalidate")
    assert again.status_code == 200 and again.json()["order_count"] == 2
    missing = client.get(f"{BASE}/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404


def test_bad_files_are_422_with_a_spanish_detail(client) -> None:
    response = upload(client, b"plain text", "pedidos.txt")

    assert response.status_code == 422 and "Formato no soportado" in response.json()["detail"]


def test_permissions_catalogs_and_outsiders(client) -> None:
    assert client.get(f"{BASE}/permissions").json() == {"can_manage": True}
    catalogs = client.get(f"{BASE}/catalogs").json()
    assert len(catalogs["reasons"]) == 8 and catalogs["statuses"][0]["label"]

    client.app.dependency_overrides[get_current_principal] = lambda: person("otro@jdh.com.co")
    assert client.get(f"{BASE}/permissions").json() == {"can_manage": False}
    assert upload(client).status_code == 403
    assert client.get(BASE).status_code == 200


def test_approve_unapprove_and_delete(client) -> None:
    batch = upload(client).json()
    orders_url = f"{BASE}/{batch['id']}/orders"

    approved = client.post(
        f"{BASE}/{batch['id']}/approvals", json={"documents": ["PV-2"], "note": "Gerencia"}
    )
    assert approved.status_code == 200
    assert approved.json()["ready_order_count"] == 2 and approved.json()["pending_line_count"] == 1
    pv2 = client.get(orders_url, params={"q": "PV-2"}).json()["items"][0]
    assert pv2["status"] == "listo_para_liberar" and pv2["status_label"] == "Listo para liberación"
    assert pv2["manual_approval"]["approved_by"] == MANAGER
    assert pv2["manual_approval"]["note"] == "Gerencia"
    assert client.get(orders_url).json()["items"][0]["manual_approval"] is None

    again = client.post(f"{BASE}/{batch['id']}/approvals", json={"documents": ["PV-2"]})
    assert again.status_code == 409
    missing = client.post(f"{BASE}/{batch['id']}/approvals", json={"documents": ["PV-9"]})
    assert missing.status_code == 404
    assert client.post(f"{BASE}/{batch['id']}/approvals", json={"documents": []}).status_code == 422

    assert client.delete(f"{orders_url}/PV-2/approval").status_code == 204
    assert client.delete(f"{orders_url}/PV-2/approval").status_code == 404
    assert client.get(f"{BASE}/{batch['id']}").json()["ready_order_count"] == 1

    assert client.delete(f"{BASE}/{batch['id']}").status_code == 204
    assert client.get(f"{BASE}/{batch['id']}").status_code == 404
    assert client.delete(f"{BASE}/{batch['id']}").status_code == 404


def test_outsiders_cannot_approve_or_delete(client) -> None:
    batch = upload(client).json()
    client.app.dependency_overrides[get_current_principal] = lambda: person("otro@jdh.com.co")

    assert (
        client.post(f"{BASE}/{batch['id']}/approvals", json={"documents": ["PV-2"]}).status_code
        == 403
    )
    assert client.delete(f"{BASE}/{batch['id']}/orders/PV-2/approval").status_code == 403
    assert client.delete(f"{BASE}/{batch['id']}").status_code == 403
