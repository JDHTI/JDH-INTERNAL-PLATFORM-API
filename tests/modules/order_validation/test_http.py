"""HTTP contract of the retained orders validator routes."""

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.order_validation.application.service import (
    _by_document,
    _same_number,
    document_key,
)
from app.modules.order_validation.domain.entities import (
    OrderLine,
    OrderView,
    ProductInfo,
    StoredLine,
)
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
    assert orders["operation_centers"] == [{"code": "004", "order_count": 2}]
    by_center = client.get(f"{BASE}/{batch['id']}/orders", params={"operation_center": "004"})
    assert by_center.json()["total"] == 2


def test_list_get_and_revalidate(client) -> None:
    batch = upload(client).json()

    listing = client.get(BASE).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == batch["id"]
    assert client.get(f"{BASE}/{batch['id']}").json()["file_name"] == "Pedidos Retenidos.xlsx"
    again = client.post(f"{BASE}/{batch['id']}/revalidate")
    assert again.status_code == 200 and again.json()["order_count"] == 2
    missing = client.get(f"{BASE}/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404


def test_a_line_stored_with_the_removed_salsamentarias_basis_shows_none(client, batches) -> None:
    batch = upload(client).json()
    batch_id = next(iter(batches.lines_by_batch))
    batches.lines_by_batch[batch_id] = [
        replace(item, result=replace(item.result, margin_basis="salsamentarias"))
        for item in batches.lines_by_batch[batch_id]
    ]

    response = client.get(f"{BASE}/{batch['id']}/orders")

    assert response.status_code == 200
    lines = [line for order in response.json()["items"] for line in order["lines"]]
    assert lines and all(line["margin_basis"] is None for line in lines)


def test_bad_files_are_422_with_a_spanish_detail(client) -> None:
    response = upload(client, b"plain text", "pedidos.txt")

    assert response.status_code == 422 and "Formato no soportado" in response.json()["detail"]


def test_permissions_catalogs_and_outsiders(client) -> None:
    assert client.get(f"{BASE}/permissions").json() == {"can_manage": True}
    catalogs = client.get(f"{BASE}/catalogs").json()
    assert len(catalogs["reasons"]) == 10 and catalogs["statuses"][0]["label"]
    assert [item["value"] for item in catalogs["alerts"]] == [
        "margen_100",
        "bajo_objetivo",
        "falta_impuesto_licor",
    ]

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
    assert approved.json()["released_order_count"] == 1
    assert approved.json()["ready_order_count"] == 1 and approved.json()["pending_line_count"] == 1
    pv2 = client.get(orders_url, params={"q": "PV-2"}).json()["items"][0]
    assert pv2["status"] == "liberado" and pv2["status_label"] == "Liberado"
    released = client.get(orders_url, params={"status": "liberado"}).json()
    assert [item["document"] for item in released["items"]] == ["PV-2"]
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
    restored = client.get(f"{BASE}/{batch['id']}").json()
    assert (restored["ready_order_count"], restored["released_order_count"]) == (1, 0)

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
    # 5 % and 10 % in the file, but the support brings the 359 line to 14,5 %: the order margin
    # counts it with its support, (5 % + 14,5 %) / 2.
    assert pv2["has_liquor_tax"] is True and Decimal(pv2["margin_pct"]) == Decimal("0.0975")
    assert [Decimal(line["order_margin_pct"]) for line in pv2["lines"]] == [
        Decimal("0.05"),
        Decimal("0.145"),
    ]
    # The 1128 line is 13 points below its 18 % and lacks the liquor tax.
    assert [alert["code"] for alert in pv2["alerts"]] == ["bajo_objetivo", "falta_impuesto_licor"]
    assert pv2["alerts"][0]["row_numbers"] == [pv2["lines"][0]["row_number"]]
    assert pv2["lines"][0]["alerts"] == ["bajo_objetivo", "falta_impuesto_licor"]
    assert Decimal(pv2["lines"][0]["points_below_target"]) == Decimal("0.13")
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
    # The sales consecutive, ascending or descending (2026-11-23).
    ascending = client.get(url, params={"sort": "document_asc"}).json()["items"]
    descending = client.get(url, params={"sort": "document_desc"}).json()["items"]
    assert [item["document"] for item in ascending] == ["PV-1", "PV-2"]
    assert [item["document"] for item in descending] == ["PV-2", "PV-1"]


def test_documents_sort_in_natural_order_with_the_ones_without_number_last() -> None:
    documents = ["PV-10", "pv-9", "PV-100", "(sin documento, fila 4)", "PV-9A", "FV-2"]
    orders = [
        OrderView(document=item, status="pendiente_validacion", lines=[_stored(row)])
        for row, item in enumerate(documents, start=2)
    ]

    ascending = [order.document for order in _by_document(orders, descending=False)]
    descending = [order.document for order in _by_document(orders, descending=True)]

    # By the PV number, whatever the prefix (2026-11-23): FV-2 and pv-9 before PV-10.
    assert ascending == ["FV-2", "pv-9", "PV-9A", "PV-10", "PV-100", "(sin documento, fila 4)"]
    assert descending == ["PV-100", "PV-10", "PV-9A", "pv-9", "FV-2", "(sin documento, fila 4)"]
    assert document_key("PV-0009") == document_key("pv-9")
    assert document_key("PV-02592133")[0] == 2592133
    assert _same_number("PV-02592133", "2592133") and not _same_number("PV-02592133", "259")


def _stored(row: int) -> StoredLine:
    return StoredLine(
        id=uuid4(),
        line=OrderLine(row_number=row, document="", customer_nit="1", sku="1"),
        result=None,  # type: ignore[arg-type]
    )


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


def test_the_rules_are_public(client) -> None:
    client.app.dependency_overrides[get_current_principal] = lambda: person("otro@jdh.com.co")

    response = client.get(f"{BASE}/rules")

    assert response.status_code == 200
    sections = response.json()
    assert [section["code"] for section in sections] == ["linea", "pedido", "aprobacion"]
    rule = sections[1]["rules"][3]
    assert rule["code"] == "tolerancia" and rule["effect"] == "libera"
    assert rule["parameters"]["margen_minimo_pedido"] == "18 %"
