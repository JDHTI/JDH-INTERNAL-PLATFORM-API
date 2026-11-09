"""HTTP contract of ``/api/v1/quotations``."""

import io
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.quotations.domain.entities import ItemInfo
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
        "sales_scope": None,
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


def test_a_local_product_goes_in_by_its_own_endpoint(client) -> None:
    quotation = client.post(BASE, json={"customer_nit": NIT}).json()
    assert quotation["lines"][0]["category"] == "ASEO"
    url = f"{BASE}/{quotation['id']}"

    created = client.post(
        f"{url}/local-lines",
        json={
            "version": 1,
            "description": " Queso costeño ",
            "supplier_name": "Lácteos del Sur",
            "price": "12000",
            "vat_pct": 19,
        },
    )

    assert created.status_code == 200
    body = created.json()
    line = body["lines"][-1]
    assert body["version"] == 2 and body["changed_count"] == 0
    assert line["sku"] == "LOC-1" and line["origin"] == "local"
    assert line["origin_label"] == "Solo cotización" and line["category"] is None
    assert line["description"] == "Queso costeño" and line["supplier_code"] is None
    assert Decimal(line["new_price"]) == Decimal(12000) and Decimal(line["vat_pct"]) == 19
    assert Decimal(line["price_with_taxes"]) == Decimal(12000)
    assert line["compliance"] is None and line["compliance_label"] is None
    assert line["variation_pct"] is None and line["support_candidates"] == []
    assert line["problems"] == ["producto_local"]
    assert line["problem_labels"][0].startswith("Producto solo para cotización")

    numeric = client.post(
        f"{url}/local-lines",
        json={"version": 2, "description": "Arepa", "price": 3500, "vat_pct": 0},
    )
    assert numeric.status_code == 200 and numeric.json()["lines"][-1]["sku"] == "LOC-2"
    invalid = client.post(
        f"{url}/local-lines",
        json={"version": 3, "description": "Arepa", "price": "3500", "vat_pct": 16},
    )
    assert invalid.status_code == 422 and invalid.json()["detail"] == "El IVA es 19, 5 o 0 %"
    missing = client.post(f"{url}/local-lines", json={"version": 3, "price": "1"})
    assert missing.status_code == 422 and "descripción" in missing.json()["detail"]
    stale = client.post(
        f"{url}/local-lines",
        json={"version": 1, "description": "Arepa", "price": "3500", "vat_pct": 0},
    )
    assert stale.status_code == 409

    skipped = client.put(
        f"{url}/lines",
        json={"version": 3, "lines": [{"sku": "LOC-1", "margin_pct": "0.3"}]},
    )
    assert skipped.status_code == 422
    removed = client.delete(f"{url}/lines/LOC-1", params={"version": 3})
    assert removed.status_code == 200
    assert [item["sku"] for item in removed.json()["lines"]] == ["2537", "LOC-2"]


def test_the_offer_contract(client) -> None:
    created = client.post(BASE, json={"customer_nit": NIT, "validity_days": 15})
    assert created.status_code == 201
    quotation = created.json()
    assert quotation["validity_days"] == 15 and quotation["valid_from"] == "2026-10-09"
    assert quotation["valid_until"] == "2026-10-24"
    assert quotation["conditions"] == [] and quotation["include_seller"] is True
    assert quotation["quote_quantities"] is False and quotation["offer_scope"] == "changed"
    assert quotation["offer_summary"] is None
    assert quotation["seller_contact"] == {
        "code": "0051",
        "name": "PEREZ ANA",
        "email": MANAGER,
        "phone": "3001234567",
    }
    line = quotation["lines"][0]
    assert line["quantity"] is None and line["in_offer"] is False
    assert Decimal(line["offer_price"]) == Decimal(4000) and line["requested_product"] is None
    assert client.post(BASE, json={"customer_nit": NIT, "validity_days": 0}).status_code == 422
    url = f"{BASE}/{quotation['id']}"

    priced = client.put(
        f"{url}/lines",
        json={"version": 1, "lines": [{"sku": "2537", "new_price": "4100", "quantity": 10}]},
    ).json()
    assert Decimal(priced["lines"][0]["quantity"]) == 10 and priced["lines"][0]["in_offer"]
    only = client.put(
        f"{url}/lines", json={"version": 2, "lines": [{"sku": "2537", "quantity": "2.5"}]}
    )
    assert only.status_code == 200 and Decimal(only.json()["lines"][0]["new_price"]) == 4100
    bad = client.put(
        f"{url}/lines", json={"version": 3, "lines": [{"sku": "2537", "quantity": "0"}]}
    )
    assert bad.status_code == 422 and "mayor que cero" in bad.json()["detail"]
    mixed = client.put(
        f"{url}/lines",
        json={"version": 3, "lines": [{"sku": "2537", "margin_pct": "0.3", "quantity": 1}]},
    )
    assert mixed.status_code == 422

    settings = client.patch(
        f"{url}/offer",
        json={
            "version": 3,
            "quote_quantities": True,
            "conditions": [" Pago a 30 días ", ""],
            "offer_scope": "all",
        },
    )
    assert settings.status_code == 200
    body = settings.json()
    assert body["version"] == 4 and body["status"] == "borrador"
    assert body["conditions"] == ["Pago a 30 días"] and body["offer_scope"] == "all"
    # 2.5 x 4100 = 10250; VAT 19 % = 1947.5, rounded half up.
    assert body["offer_summary"] == {
        "item_count": 1,
        "subtotal": "10250",
        "vat_groups": [{"vat_pct": "19", "base": "10250", "vat": "1948"}],
        "liquor_tax": "0",
        "total": "12198",
    }
    assert client.patch(f"{url}/offer", json={"version": 3}).status_code == 409
    too_many = client.patch(f"{url}/offer", json={"version": 4, "conditions": ["a"] * 21})
    assert too_many.status_code == 422 and "20 condiciones" in too_many.json()["detail"]

    workbook = client.get(f"{url}/offer.xlsx")
    assert workbook.status_code == 200
    assert (
        "Cotizacion%20HOTEL%20EL%20BOSQUE%20SAS%202026-10-09.xlsx"
        in (workbook.headers["content-disposition"])
    )
    sheet = load_workbook(io.BytesIO(workbook.content)).active
    assert sheet["A1"].value == "Cotización"


def test_the_requested_products_contract(client) -> None:
    quotation = client.post(BASE, json={"customer_nit": NIT}).json()
    url = f"{BASE}/{quotation['id']}"
    created = client.post(
        f"{url}/local-lines",
        json={"version": 1, "description": "Queso costeño", "price": 12000, "vat_pct": 5},
    ).json()
    reference = created["lines"][-1]["requested_product"]
    assert reference["status"] == "solicitado" and reference["siesa_sku"] is None

    listing = client.get(f"{BASE}/requested-products", params={"status": "solicitado"})
    assert listing.status_code == 200
    page = listing.json()
    assert page["total"] == 1 and page["page"] == 1 and page["size"] == 20
    item = page["items"][0]
    assert item["id"] == reference["id"] and item["status_label"] == "Solicitado"
    assert Decimal(item["estimated_price"]) == 12000 and item["created_by"] == MANAGER
    assert item["quotations"] == [
        {
            "id": quotation["id"],
            "customer_name": "HOTEL EL BOSQUE SAS",
            "customer_nit": NIT,
            "status": "borrador",
            "status_label": "Borrador",
            "created_by": MANAGER,
        }
    ]
    target = f"{BASE}/requested-products/{reference['id']}"
    assert client.patch(target, json={"status": "creado", "siesa_sku": "26278"}).status_code == 403
    refused = client.post(f"{url}/lines/LOC-1/promote", json={"version": 2})
    assert refused.status_code == 422

    client.app.dependency_overrides[get_current_principal] = lambda: person(APPROVER)
    missing = client.patch(target, json={"status": "creado", "siesa_sku": "404"})
    assert missing.status_code == 422 and "404" in missing.json()["detail"]
    done = client.patch(target, json={"status": "creado", "siesa_sku": "26278", "note": "Listo"})
    assert done.status_code == 200
    assert done.json()["status"] == "creado" and done.json()["siesa_sku"] == "26278"
    assert done.json()["note"] == "Listo"

    promoted = client.post(f"{url}/lines/LOC-1/promote", json={"version": 2})
    assert promoted.status_code == 200
    assert [line["sku"] for line in promoted.json()["lines"]] == ["2537", "26278"]
    assert promoted.json()["lines"][-1]["origin"] == "agregado"


def test_categories_are_filled_on_read_and_never_break_it(client, warehouse, repository) -> None:
    quotation = client.post(BASE, json={"customer_nit": NIT}).json()
    stored = repository.items[UUID(quotation["id"])]
    old = replace(stored, lines=tuple(replace(line, category=None) for line in stored.lines))
    repository.items[old.quotation.id] = old
    url = f"{BASE}/{quotation['id']}"

    warehouse.down = True
    down = client.get(url)
    assert down.status_code == 200 and down.json()["lines"][0]["category"] is None

    warehouse.down = False
    warehouse.item_rows["2537"] = ItemInfo("2537", "Abrasivo", "UNID", category="ASEO")
    filled = client.get(url).json()
    assert filled["lines"][0]["category"] == "ASEO" and filled["version"] == 1
    assert filled["updated_at"] == quotation["updated_at"]


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
