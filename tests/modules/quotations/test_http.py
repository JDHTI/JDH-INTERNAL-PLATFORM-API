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
from tests.modules.quotations.conftest import APPROVER, MANAGER, NIT
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
        "can_validate": False,
        "seller_linked": True,
        "seller": {"code": "0051", "name": "PEREZ ANA", "email": MANAGER},
    }
    sections = client.get(f"{BASE}/rules").json()
    assert [section["code"] for section in sections] == ["precio", "margen", "regla", "formato"]
    customers = client.get(f"{BASE}/customers", params={"scope": "mine"}).json()
    assert customers["seller_linked"] and [item["nit"] for item in customers["items"]] == [NIT]
    assert customers["items"][0]["is_active"] is True


def test_quote_price_confirm_validate_and_download(client) -> None:
    created = client.post(BASE, json={"customer_nit": NIT, "window_months": 12})
    assert created.status_code == 201
    quotation = created.json()
    assert quotation["status"] == "borrador" and quotation["status_label"] == "Borrador"
    assert quotation["list_action"] == "actualizacion" and quotation["regional"] == "nacional"
    assert quotation["price_family_label"] == "Generales"
    assert {"code": "lista_compartida", "label": quotation["warnings"][0]["label"]} in quotation[
        "warnings"
    ]
    line = quotation["lines"][0]
    assert line["sku"] == "2537" and Decimal(line["last_price"]) == Decimal("4000")
    assert Decimal(line["min_price"]) == Decimal("3719")
    assert Decimal(line["optimal_price"]) == Decimal("3765")
    assert line["cost_source"] == "inventario" and line["is_active"] is True
    assert line["margin_basis_label"] == "Margen Lista 1" and line["compliance"] is None
    url = f"{BASE}/{quotation['id']}"

    below = client.put(
        f"{url}/lines", json={"version": 1, "lines": [{"sku": "2537", "new_price": "3600"}]}
    )
    assert below.status_code == 200 and below.json()["skipped"] == []
    assert below.json()["lines"][0]["compliance_label"] == "Bajo el mínimo"
    stale = client.put(
        f"{url}/lines", json={"version": 1, "lines": [{"sku": "2537", "new_price": "3800"}]}
    )
    assert stale.status_code == 409
    both = client.put(
        f"{url}/lines",
        json={"version": 2, "lines": [{"sku": "2537", "new_price": "1", "margin_pct": "0.3"}]},
    )
    assert both.status_code == 422
    refused = client.post(f"{url}/confirm", json={"version": 2})
    assert refused.status_code == 422 and "no cumplen" in refused.json()["detail"]
    assert client.get(f"{url}/export.txt").status_code == 409

    at_margin = client.put(
        f"{url}/lines", json={"version": 2, "lines": [{"sku": "2537", "margin_pct": "0.3"}]}
    )
    assert Decimal(at_margin.json()["lines"][0]["new_price"]) == Decimal("4303")
    confirmed = client.post(f"{url}/confirm", json={"version": 3, "activation_date": "2026-10-15"})
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "pendiente_validacion"
    assert confirmed.json()["export_price_list_id"] == "001"
    assert client.post(f"{url}/validate", json={"version": 4}).status_code == 403

    client.app.dependency_overrides[get_current_principal] = lambda: person(APPROVER)
    validated = client.post(f"{url}/validate", json={"version": 4})
    assert validated.status_code == 200 and validated.json()["status"] == "actualizada"
    assert validated.json()["status_label"] == "Actualizada"

    flat = client.get(f"{url}/export.txt")
    assert flat.status_code == 200 and flat.headers["cache-control"] == "no-store"
    assert "precios-lista001-900315138-20261015.txt" in flat.headers["content-disposition"]
    assert [len(item) for item in flat.content.split(b"\r\n")] == [18, 254, 18, 0]
    workbook = client.get(f"{url}/export.xlsx")
    sheet = load_workbook(io.BytesIO(workbook.content))["Precios"]
    assert list(sheet.iter_rows(min_row=2, values_only=True)) == [
        ("001", 2537, 20261015, "UNID", 4303)
    ]
    template = client.get(f"{url}/template.xlsx")
    assert template.status_code == 200
    assert "cotizacion-900315138-lista001.xlsx" in template.headers["content-disposition"]

    listing = client.get(BASE, params={"mine": "false"}).json()
    assert listing["total"] == 1 and listing["items"][0]["changed_count"] == 1
    assert listing["items"][0]["validated_at"] is not None
    client.app.dependency_overrides[get_current_principal] = lambda: person(MANAGER, admin=True)
    assert client.delete(url).status_code == 204
    assert client.get(url).status_code == 404


def test_the_workbook_goes_up_as_multipart(client) -> None:
    quotation = client.post(BASE, json={"customer_nit": NIT}).json()
    url = f"{BASE}/{quotation['id']}"
    book = load_workbook(io.BytesIO(client.get(f"{url}/template.xlsx").content))
    sheet = book["Cotizacion"]
    header = [cell.value for cell in sheet[1]]
    sheet.cell(2, header.index("Precio nuevo") + 1).value = 4100
    buffer = io.BytesIO()
    book.save(buffer)

    uploaded = client.post(
        f"{url}/template",
        data={"version": "1"},
        files={"file": ("cotizacion.xlsx", buffer.getvalue(), "application/octet-stream")},
    )

    assert uploaded.status_code == 200
    body = uploaded.json()
    assert body["template_result"]["updated"] == ["2537"]
    assert Decimal(body["lines"][0]["new_price"]) == Decimal("4100")
    assert body["version"] == 2


def test_paste_adds_revalidates_and_reports(client) -> None:
    quotation = client.post(BASE, json={"customer_nit": NIT}).json()

    pasted = client.post(
        f"{BASE}/{quotation['id']}/lines",
        json={"version": 1, "text": "26278\r\n2537\r\n404\r\n26278"},
    )

    assert pasted.status_code == 200
    body = pasted.json()
    assert body["import_result"] == {
        "added": ["26278"],
        "revalidated": ["2537"],
        "not_found": ["404"],
        "inactive": [],
        "repeated": 1,
    }
    assert [line["origin"] for line in body["lines"]] == ["historico", "agregado"]
    assert body["lines"][1]["origin_label"] == "Agregado" and body["lines"][1]["last_price"] is None
    removed = client.delete(f"{BASE}/{quotation['id']}/lines/26278", params={"version": 2})
    assert removed.status_code == 200 and len(removed.json()["lines"]) == 1
    nothing = client.post(f"{BASE}/{quotation['id']}/lines", json={"version": 3, "text": "404"})
    assert nothing.status_code == 422 and "404" in nothing.json()["detail"]


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
