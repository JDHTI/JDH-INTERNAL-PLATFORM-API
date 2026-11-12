"""HTTP contract of the creation form schema, the bulk load and the supplier profiles."""

import io
import json
from types import SimpleNamespace

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.identity.presentation.principal import get_current_principal
from tests.modules.supplier_supports.conftest import person

BASE = "/api/v1/supplier-supports"
PROFILES = "/api/v1/supplier-support-profiles"
FILE = (
    "Tipo,Método de cálculo,Valor Proveedor,Referencia JDH,NIT Cliente,Fecha Inicio\n"
    "tactico_cliente,pct_costo,5%,1128,800116299,01/10/2026\n"
    "tactico_cliente,pct_costo,7%,8749,,01/10/2026\n"
)


class AllowAll:
    async def allow_sliding_window(self, bucket, limit, window):
        return SimpleNamespace(allowed=True, count=1, retry_after=0)

    async def close(self):
        return None


@pytest.fixture
def client(service, bulk, profiles):
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[get_current_principal] = lambda: person("comercial@jdh.com.co")
    container = app.state.container.v1_container
    with (
        app.state.container.redis.override(AllowAll()),
        container.supplier_support_service.override(service),
        container.supplier_support_bulk_service.override(bulk),
        container.supplier_support_profile_service.override(profiles),
        TestClient(app) as test_client,
    ):
        yield test_client
    app.dependency_overrides.clear()


def upload(client, content=FILE, name="apoyos.csv", **params):
    return client.post(
        f"{BASE}/bulk",
        params={"supplier_code": "233", **params},
        files={"file": (name, content.encode(), "text/csv")},
    )


def test_form_schema_is_not_swallowed_by_the_support_id_route(client) -> None:
    response = client.get(f"{BASE}/form-schema")

    assert response.status_code == 200
    body = response.json()
    names = {item["name"] for item in body["fields"]}
    assert {"kind", "calc_method", "supplier_value", "product_sku", "regionals"} <= names
    assert body["methods"]["precio_neto"]["requires"] == ["supplier_value", "target_margin_pct"]
    assert body["methods"]["pct_costo"]["value_kind"] == "percent"
    kind = next(item for item in body["fields"] if item["name"] == "kind")
    assert "tactico_cliente" in kind["choices"]


def test_bulk_simulates_then_applies(client) -> None:
    simulated = upload(client)

    assert simulated.status_code == 200
    body = simulated.json()
    assert body["applied"] is False and body["blocked"] is False and body["valid"] == 2
    assert body["preview"][0]["supplier_value"] in ("0.05", "0.050000") or (
        float(body["preview"][0]["supplier_value"]) == 0.05
    )
    assert client.post(f"{BASE}/search", json={}).json()["total"] == 0

    applied = upload(client, apply="true").json()

    assert applied["applied"] is True and len(applied["created"]) == 2
    assert client.post(f"{BASE}/search", json={}).json()["total"] == 2


def test_bulk_reports_errors_and_saves_nothing(client) -> None:
    bad = FILE + "tactico_cliente,pct_costo,7%,999999,,01/10/2026\n"

    body = upload(client, bad, apply="true").json()

    assert body["blocked"] is True and body["applied"] is False
    assert body["errors"] == [
        {"row_number": 4, "field": "product_sku", "message": "Referencia JDH desconocida: 999999"}
    ]
    assert client.post(f"{BASE}/search", json={}).json()["total"] == 0


def test_bulk_accepts_defaults_and_column_map_as_json_form_fields(client) -> None:
    response = client.post(
        f"{BASE}/bulk",
        params={"supplier_code": "233"},
        data={
            "defaults": json.dumps({"kind": "actividad_temporal", "calc_method": "pct_costo"}),
            "column_map": json.dumps({"supplier_value": ["DESCUENTO PACTADO"]}),
        },
        files={"file": ("a.csv", b"SKU,DESCUENTO PACTADO\n1128,0.1\n", "text/csv")},
    )

    assert response.status_code == 200 and response.json()["valid"] == 1
    broken = client.post(
        f"{BASE}/bulk",
        data={"column_map": "{no es json"},
        files={"file": ("a.csv", b"x", "text/csv")},
    )
    assert broken.status_code == 422 and "JSON" in broken.json()["detail"]


def test_bulk_is_for_managers_and_rejects_unknown_formats(client) -> None:
    assert upload(client, name="apoyos.pdf").status_code == 422
    client.app.dependency_overrides[get_current_principal] = lambda: person("otro@jdh.com.co")

    assert upload(client).status_code == 403


def test_template_is_a_workbook_that_follows_the_profile(client) -> None:
    client.put(
        f"{PROFILES}/233",
        json={
            "defaults": {"calc_method": "pct_costo"},
            "column_map": {"supplier_value": ["% APOYO"]},
        },
    )

    general = client.get(f"{BASE}/bulk/template")
    own = client.get(f"{BASE}/bulk/template", params={"supplier_code": "233"})
    full = client.get(f"{BASE}/bulk/template", params={"full": "true"})

    assert general.status_code == 200 and "spreadsheetml" in general.headers["content-type"]
    headers = {
        key: [cell.value for cell in openpyxl.load_workbook(io.BytesIO(r.content))["Apoyos"][1]]
        for key, r in (("general", general), ("own", own), ("full", full))
    }
    assert "Tipo de apoyo" in headers["general"] and "% APOYO" not in headers["general"]
    assert "Tipo de apoyo" not in headers["own"] and "% APOYO" in headers["own"]
    assert "Clase de apoyo" not in headers["general"] and "Clase de apoyo" in headers["full"]
    assert openpyxl.load_workbook(io.BytesIO(general.content)).sheetnames == [
        "Apoyos",
        "Instrucciones",
        "Valores",
    ]


def test_profile_lifecycle(client) -> None:
    body = {
        "defaults": {"kind": "tactico_cliente", "calc_method": "pct_costo", "jdh_pct": "1%"},
        "column_map": {"product_sku": ["REF JDH"]},
        "sheet": "Apoyos",
    }

    created = client.put(f"{PROFILES}/233", json=body)

    assert created.status_code == 200
    assert created.json()["supplier_name"] == "FAMILIA SANCELA" and created.json()["version"] == 1
    assert created.json()["defaults"]["jdh_pct"] == "0.01"
    assert client.get(f"{PROFILES}/233").json()["sheet"] == "Apoyos"
    assert [item["supplier_code"] for item in client.get(PROFILES).json()] == ["233"]
    assert client.put(f"{PROFILES}/233", json=body).status_code == 409  # exists, no version
    replaced = client.put(f"{PROFILES}/233", json={**body, "version": 1, "notes": "ok"})
    assert replaced.json()["version"] == 2 and replaced.json()["notes"] == "ok"
    assert client.put(f"{PROFILES}/233", json={**body, "version": 1}).status_code == 409
    assert client.delete(f"{PROFILES}/233").status_code == 204
    assert client.get(f"{PROFILES}/233").status_code == 404


def test_profile_errors(client) -> None:
    assert client.get(f"{PROFILES}/nope").status_code == 404
    assert client.put(f"{PROFILES}/nope", json={}).status_code == 404
    assert (
        client.put(f"{PROFILES}/233", json={"defaults": {"kind": "inventado"}}).status_code == 422
    )
    assert client.put(f"{PROFILES}/233", json={"surprise": 1}).status_code == 422
    client.app.dependency_overrides[get_current_principal] = lambda: person("otro@jdh.com.co")
    assert client.put(f"{PROFILES}/233", json={}).status_code == 403
    assert client.get(PROFILES).status_code == 200  # reading is open to any user
