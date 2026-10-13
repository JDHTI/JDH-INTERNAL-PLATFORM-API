"""HTTP contract of the commercial master routes."""

import io
from types import SimpleNamespace
from uuid import uuid4

import openpyxl
import pytest
from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.commercial_masters.application.service import CommercialMasterService
from app.modules.commercial_masters.infrastructure.margins_file import SpreadsheetMarginReader
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from fastapi.testclient import TestClient

from tests.modules.commercial_masters.test_service import Managers, Repo

BASE = "/api/v1/commercial"
CSV = "text/csv"


class AllowAll:
    async def allow_sliding_window(self, bucket, limit, window):
        return SimpleNamespace(allowed=True, count=1, retry_after=0)

    async def close(self):
        return None


def make_client(admin: bool):
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    who = Principal(uuid4(), "ana@jdh.com.co", "Ana", admin)
    app.dependency_overrides[get_current_principal] = lambda: who
    container = app.state.container
    repo = Repo()
    service = CommercialMasterService(
        repo, Managers(), margin_reader=SpreadsheetMarginReader(), max_upload_bytes=1024 * 1024
    )
    return (
        app,
        repo,
        container.redis.override(AllowAll()),
        (container.v1_container.commercial_master_service.override(service)),
    )


@pytest.fixture
def api():
    app, repo, redis, service = make_client(admin=True)
    with redis, service, TestClient(app) as client:
        yield client, repo


@pytest.fixture
def outsider_api():
    app, repo, redis, service = make_client(admin=False)
    with redis, service, TestClient(app) as client:
        yield client, repo


def test_commercial_suppliers_are_listed_with_paging(api) -> None:
    client, repo = api

    response = client.get(f"{BASE}/suppliers", params={"q": "familia", "page": 2, "size": 10})

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["code"] == "233" and body["page"] == 2 and body["size"] == 10
    assert "list_margin_pct" not in body["items"][0]
    assert repo.calls[-1] == ("suppliers", "familia", None, 2, 10)
    assert client.get(f"{BASE}/suppliers", params={"size": 1000}).status_code == 422
    assert client.get(f"{BASE}/suppliers", params={"page": 0}).status_code == 422


def test_unknown_commercial_supplier_is_404(api) -> None:
    client, _ = api

    assert client.get(f"{BASE}/suppliers/233").status_code == 200
    assert client.get(f"{BASE}/suppliers/0").status_code == 404


def test_customers_accept_the_documented_filters(api) -> None:
    client, repo = api

    response = client.get(
        f"{BASE}/customers",
        params={
            "nit": "800.116.299",
            "channel": "RESTAURANTES",
            "is_active": "true",
            "size": 5,
        },
    )

    assert response.status_code == 200
    assert repo.calls[-1][:4] == ("customers", None, "800116299", "RESTAURANTES")
    assert repo.calls[-1][6] is True


def test_active_and_margin_filters_of_legal_suppliers_and_products(api) -> None:
    client, repo = api

    legal = client.get(f"{BASE}/legal-suppliers", params={"is_active": "false"})
    assert legal.status_code == 200 and repo.calls[-1][3] is False
    response = client.get(f"{BASE}/products", params={"is_active": "true", "has_margin": "false"})
    assert response.status_code == 200
    assert repo.calls[-1][5] is True and repo.calls[-1][-1] is False


def test_patch_of_a_supplier_only_handles_nits(api) -> None:
    client, repo = api

    response = client.patch(f"{BASE}/suppliers/233", json={"confirmed_nits": ["890900161"]})

    assert response.status_code == 200
    assert repo.calls[-1] == ("update", "233", {}, ["890900161"], None)


def test_a_product_shows_its_margins(api) -> None:
    client, _ = api

    body = client.get(f"{BASE}/products/7244").json()

    assert body["sku"] == "7244" and body["has_margin"] is False
    assert body["margin_list"] is None and body["liquor_tax"] is None
    assert client.get(f"{BASE}/products/0").status_code == 404


def test_patch_a_product_and_its_margins(api) -> None:
    client, repo = api

    response = client.patch(
        f"{BASE}/products/7244", json={"margin_list": "0.2", "margin_wholesale": None}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["margin_list"] == "0.2" and body["has_margin"] is True
    assert body["margin_source"] == "manual" and body["margin_updated_by"] == "ana@jdh.com.co"
    assert repo.calls[-1][2].keys() == {"margin_list", "margin_wholesale"}
    assert client.patch(f"{BASE}/products/7244", json={"margin_list": 18}).status_code == 422
    assert client.patch(f"{BASE}/products/7244", json={"state": "Activo"}).status_code == 422


def test_margin_file_simulation_then_apply(api) -> None:
    client, repo = api
    text = "Referencia;Margen Lista 1;Observaciones\n7244;18;Aseo\n0000;18;\n"
    files = {"file": ("margenes.csv", text.encode(), CSV)}

    preview = client.post(f"{BASE}/products/margins", files=files)

    assert preview.status_code == 200
    body = preview.json()
    assert body["applied"] is False and body["updated"] == 1 and body["unknown_skus"] == ["0000"]
    assert body["changes"][0]["margins"]["margin_list"] == {"from": None, "to": "0.180000"}
    assert body["changes"][0]["notes"] == {"from": None, "to": "Aseo"}
    assert not any(call[0] == "bulk" for call in repo.calls)

    applied = client.post(f"{BASE}/products/margins", params={"apply": "true"}, files=files).json()
    assert applied["applied"] is True and repo.calls[-1][0] == "bulk"
    assert (
        client.post(f"{BASE}/products/margins", files={"file": ("m.txt", b"x", CSV)}).status_code
        == 422
    )


def test_margin_template_is_the_editable_workbook(api) -> None:
    client, _ = api

    response = client.get(f"{BASE}/products/margins/template")

    assert response.status_code == 200
    assert "margenes-productos.xlsx" in response.headers["content-disposition"]
    book = openpyxl.load_workbook(io.BytesIO(response.content))
    sheet = book["Margen Producto"]
    assert [cell.value for cell in sheet[1]][:2] == ["Referencia", "Desc. item"]
    assert {row[0].value for row in sheet.iter_rows(min_row=2)} == {"7244", "1569"}
    empty = client.get(f"{BASE}/products/margins/template", params={"with_data": "false"})
    assert openpyxl.load_workbook(io.BytesIO(empty.content))["Margen Producto"].max_row == 1


def test_non_managers_can_read_but_not_edit(outsider_api) -> None:
    client, _ = outsider_api
    files = {"file": ("margenes.csv", b"Referencia;Margen Lista 1\n7244;18\n", CSV)}

    assert client.patch(f"{BASE}/suppliers/233", json={"confirmed_nits": []}).status_code == 403
    assert client.patch(f"{BASE}/products/7244", json={"margin_list": "0.2"}).status_code == 403
    assert client.post(f"{BASE}/products/margins", files=files).status_code == 403
    assert client.get(f"{BASE}/suppliers/233").status_code == 200  # reading is open
    assert client.get(f"{BASE}/products/7244").status_code == 200
    assert client.get(f"{BASE}/products/margins/template").status_code == 200
