"""HTTP contract of ``/api/v1/quotations``."""

import io
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.identity.presentation.principal import get_current_principal
from tests.modules.quotations.conftest import MANAGER, NIT
from tests.modules.supplier_supports.conftest import person

BASE = "/api/v1/quotations"


class AllowAll:
    async def allow_sliding_window(self, bucket, limit, window):
        return SimpleNamespace(allowed=True, count=1, retry_after=0)

    async def close(self):
        return None


@pytest.fixture
def client(service):
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    advisor = person(MANAGER)
    app.dependency_overrides[get_current_principal] = lambda: advisor
    container = app.state.container
    with (
        container.redis.override(AllowAll()),
        container.v1_container.quotation_service.override(service),
        TestClient(app) as test_client,
    ):
        yield test_client
    app.dependency_overrides.clear()


def test_fixed_paths_are_not_taken_for_an_id(client) -> None:
    permissions = client.get(f"{BASE}/permissions").json()
    assert permissions == {
        "can_manage": True,
        "seller_linked": True,
        "seller": {"code": "0051", "name": "PEREZ ANA", "email": MANAGER},
    }
    sections = client.get(f"{BASE}/rules").json()
    assert [section["code"] for section in sections] == ["precio", "margen", "regla", "formato"]
    customers = client.get(f"{BASE}/customers", params={"scope": "mine"}).json()
    assert customers["seller_linked"] and [item["nit"] for item in customers["items"]] == [NIT]
    assert customers["items"][0]["is_active"] is True


def test_quote_price_generate_and_download(client) -> None:
    created = client.post(BASE, json={"customer_nit": NIT, "window_months": 12})
    assert created.status_code == 201
    quotation = created.json()
    assert quotation["status"] == "borrador" and quotation["status_label"] == "Borrador"
    assert {"code": "lista_compartida", "label": quotation["warnings"][0]["label"]} in quotation[
        "warnings"
    ]
    line = quotation["lines"][0]
    assert line["sku"] == "2537" and Decimal(line["last_price"]) == Decimal("4000")
    assert Decimal(line["min_price"]) == Decimal("3703.7038")
    assert line["margin_basis_label"] == "Margen Lista 1" and line["compliance"] is None

    below = client.put(
        f"{BASE}/{quotation['id']}/lines",
        json={"version": 1, "lines": [{"sku": "2537", "new_price": "3600"}]},
    )
    assert below.status_code == 200
    assert below.json()["lines"][0]["compliance_label"] == "Bajo el mínimo"
    stale = client.put(
        f"{BASE}/{quotation['id']}/lines",
        json={"version": 1, "lines": [{"sku": "2537", "new_price": "3800"}]},
    )
    assert stale.status_code == 409
    refused = client.post(f"{BASE}/{quotation['id']}/generate", json={"version": 2})
    assert refused.status_code == 422 and "no cumplen" in refused.json()["detail"]
    assert client.get(f"{BASE}/{quotation['id']}/export.txt").status_code == 409

    client.put(
        f"{BASE}/{quotation['id']}/lines",
        json={"version": 2, "lines": [{"sku": "2537", "new_price": "3800"}]},
    )
    generated = client.post(
        f"{BASE}/{quotation['id']}/generate",
        json={"version": 3, "activation_date": "2026-10-15"},
    )
    assert generated.status_code == 200 and generated.json()["status"] == "generado"

    flat = client.get(f"{BASE}/{quotation['id']}/export.txt")
    assert flat.status_code == 200 and flat.headers["cache-control"] == "no-store"
    assert "precios-lista1-900315138-20261015.txt" in flat.headers["content-disposition"]
    assert [len(item) for item in flat.content.split(b"\r\n")] == [18, 254, 18, 0]
    workbook = client.get(f"{BASE}/{quotation['id']}/export.xlsx")
    sheet = load_workbook(io.BytesIO(workbook.content))["Precios"]
    assert list(sheet.iter_rows(min_row=2, values_only=True)) == [
        ("1", "2537", "20261015", "UNID", 3800)
    ]

    listing = client.get(BASE).json()
    assert listing["total"] == 1 and listing["items"][0]["changed_count"] == 1
    assert client.delete(f"{BASE}/{quotation['id']}").status_code == 204
    assert client.get(f"{BASE}/{quotation['id']}").status_code == 404


def test_errors_are_spanish_and_typed(client, warehouse) -> None:
    assert client.post(BASE, json={"customer_nit": NIT, "window_months": 3}).status_code == 422
    assert client.post(BASE, json={"customer_nit": "111"}).status_code == 404
    warehouse.down = True
    unavailable = client.post(BASE, json={"customer_nit": NIT})
    assert unavailable.status_code == 503 and "datawarehouse" in unavailable.json()["detail"]
    assert client.get(f"{BASE}/permissions").status_code == 200


def test_people_outside_the_area_are_refused(client) -> None:
    client.app.dependency_overrides[get_current_principal] = lambda: person("otro@jdh.com.co")

    assert client.get(f"{BASE}/permissions").json()["can_manage"] is False
    assert client.post(BASE, json={"customer_nit": NIT}).status_code == 403
    assert client.get(BASE).status_code == 403
