"""HTTP contract of the retained orders validator routes."""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.order_validation.domain.entities import ProductInfo
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

    orders = client.get(f"{BASE}/{batch['id']}/orders", params={"sort": "file"}).json()
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
    assert len(catalogs["reasons"]) == 9 and catalogs["statuses"][0]["label"]

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
    in_file = client.get(orders_url, params={"sort": "file"}).json()["items"]
    assert in_file[0]["manual_approval"] is None

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


def test_orders_carry_the_margin_flag_and_sort(client, source) -> None:
    source.catalog["1128"] = ProductInfo("1128", {"margin_list": Decimal("0.18")}, Decimal("6911"))
    batch = upload(client).json()
    url = f"{BASE}/{batch['id']}/orders"

    default = client.get(url).json()
    # PV-2 has a line with no support, so it leads; its lines too.
    assert [item["document"] for item in default["items"]] == ["PV-2", "PV-1"]
    pv2 = default["items"][0]
    assert [line["sku"] for line in pv2["lines"]] == ["1128", "28703"]
    assert pv2["has_liquor_tax"] is True and Decimal(pv2["margin_pct"]) == Decimal("0.075")
    # The 359 line aims at its support (14 %), the 233 line at the product margin (18 %).
    assert Decimal(pv2["required_margin_pct"]) == Decimal("0.16")
    assert pv2["without_support_line_count"] == 1 and pv2["ready_line_count"] == 1
    assert pv2["release_basis"] is None and pv2["margin_with_threshold_pct"] is None
    first = pv2["lines"][0]
    assert first["liquor_tax"] is True and Decimal(first["liquor_tax_amount"]) == 6911
    assert first["margin_basis"] == "lista" and first["checks"] == []
    assert "threshold_applied" not in str(default)
    assert batch["margin_threshold_points"] == "0.00"

    assert default["items"][1]["release_basis"] == "lineas"
    in_file = client.get(url, params={"sort": "file"}).json()["items"]
    assert [item["document"] for item in in_file] == ["PV-1", "PV-2"]
    assert client.get(url, params={"sort": "margen"}).status_code == 422


def test_threshold_on_upload_try_save_and_revalidate(client, source) -> None:
    source.items.clear()
    # PV-1 is a 10 % line against a 10,5 % margin: short by half a point.
    source.catalog["18294"] = ProductInfo("18294", {"margin_list": Decimal("0.105")})
    files = {"file": ("Pedidos Retenidos.xlsx", report(), XLSX)}

    created = client.post(BASE, files=files, data={"margin_threshold_points": "0.5"})

    assert created.status_code == 201
    batch = created.json()
    assert batch["margin_threshold_points"] == "0.50" and batch["ready_order_count"] == 1
    assert batch["ready_line_count"] == 0  # the threshold acts on orders, not on lines
    url = f"{BASE}/{batch['id']}"
    page = client.get(f"{url}/orders", params={"sort": "file"}).json()
    assert page["threshold_released_count"] == 1
    pv1 = page["items"][0]
    assert pv1["released_by_threshold"] is True
    assert pv1["status"] == "listo_para_liberar" and pv1["release_basis"] == "margen"
    assert Decimal(pv1["margin_pct"]) == Decimal("0.1")
    assert Decimal(pv1["margin_with_threshold_pct"]) == Decimal("0.105")
    assert pv1["lines"][0]["reason"] == "sin_acuerdo"

    # Trying another value changes the page, not the saved file.
    tried = client.get(f"{url}/orders", params={"margin_threshold_points": "0"}).json()
    assert tried["counts"]["listo_para_liberar"] == 0 and tried["threshold_released_count"] == 0
    assert client.get(url).json()["ready_order_count"] == 1
    assert client.get(f"{url}/orders", params={"margin_threshold_points": "25"}).status_code == 422

    saved = client.put(f"{url}/margin-threshold", json={"margin_threshold_points": "0"})
    assert saved.status_code == 200 and saved.json()["margin_threshold_points"] == "0.00"
    assert saved.json()["ready_order_count"] == 0
    assert (
        client.put(f"{url}/margin-threshold", json={"margin_threshold_points": 25}).status_code
        == 422
    )
    assert client.put(f"{url}/margin-threshold", json={}).status_code == 422
    revalidated = client.post(f"{url}/revalidate", json={"margin_threshold_points": "0.5"})
    assert revalidated.json()["margin_threshold_points"] == "0.50"
    assert revalidated.json()["ready_order_count"] == 1
    kept = client.post(f"{url}/revalidate")
    assert kept.json()["margin_threshold_points"] == "0.50"
    bad = client.post(BASE, files=files, data={"margin_threshold_points": "-1"})
    assert bad.status_code == 422


def test_outsiders_cannot_save_the_threshold(client) -> None:
    batch = upload(client).json()
    client.app.dependency_overrides[get_current_principal] = lambda: person("otro@jdh.com.co")

    denied = client.put(
        f"{BASE}/{batch['id']}/margin-threshold", json={"margin_threshold_points": 1}
    )

    assert denied.status_code == 403
    assert (
        client.get(
            f"{BASE}/{batch['id']}/orders", params={"margin_threshold_points": 1}
        ).status_code
        == 200
    )
