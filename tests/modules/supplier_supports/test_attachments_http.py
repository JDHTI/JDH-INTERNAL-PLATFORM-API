"""HTTP contract of the supporting documents of supports."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.identity.presentation.principal import get_current_principal
from tests.modules.supplier_supports.conftest import person
from tests.modules.supplier_supports.test_http import NEW

BASE = "/api/v1/supplier-supports"
PNG = ("correo.png", b"\x89PNG screenshot", "image/png")


class AllowAll:
    async def allow_sliding_window(self, bucket, limit, window):
        return SimpleNamespace(allowed=True, count=1, retry_after=0)

    async def close(self):
        return None


@pytest.fixture
def client(service, attachments):
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[get_current_principal] = lambda: person("comercial@jdh.com.co")
    container = app.state.container.v1_container
    with (
        app.state.container.redis.override(AllowAll()),
        container.supplier_support_service.override(service),
        container.supplier_support_attachment_service.override(attachments),
        TestClient(app) as test_client,
    ):
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def supports(client):
    return [
        client.post(BASE, json={**NEW, "product_sku": sku, "customer_nit": None}).json()["id"]
        for sku in ("1128", "8749")
    ]


def test_upload_to_one_support_then_list_and_unlink(client, supports) -> None:
    created = client.post(
        f"{BASE}/{supports[0]}/attachments",
        files=[("files", PNG)],
        data={"description": "Correo del asesor", "tags": "correo, octubre"},
    )

    assert created.status_code == 201
    body = created.json()
    assert body["supports"] == 1 and body["links_created"] == 1 and len(body["documents"]) == 1
    listed = client.get(f"{BASE}/{supports[0]}/attachments").json()
    assert [item["document_id"] for item in listed] == body["documents"]
    assert listed[0]["support_count"] == 1 and listed[0]["linked_by"] == "comercial@jdh.com.co"
    assert client.get(f"{BASE}/{supports[1]}/attachments").json() == []

    url = f"{BASE}/{supports[0]}/attachments/{body['documents'][0]}"
    assert client.delete(url).status_code == 204
    assert client.delete(url).status_code == 404
    assert client.get(f"{BASE}/{supports[0]}/attachments").json() == []


def test_a_support_file_is_opened_from_the_master(client, supports) -> None:
    uploaded = client.post(
        f"{BASE}/{supports[0]}/attachments", files=[("files", PNG)], data={"name": "Correo"}
    ).json()
    document = uploaded["documents"][0]
    url = f"{BASE}/{supports[0]}/attachments/{document}/view"
    client.app.dependency_overrides[get_current_principal] = lambda: person("lector@jdh.com.co")

    shown = client.get(url)
    saved = client.get(url, params={"download": "true"})

    assert shown.status_code == 200
    body = shown.json()
    assert body["inline"] is True and body["url"].endswith("inline") and body["name"] == "Correo"
    assert body["content_type"] == "image/png" and body["expires_in"] == 300
    assert saved.json()["inline"] is False and saved.json()["url"].endswith("attachment")
    assert client.get(f"{BASE}/{supports[1]}/attachments/{document}/view").status_code == 404


def test_one_upload_for_a_selection_found_by_references(client, supports) -> None:
    selection = {"search": {"product_skus": ["1128", "8749"]}}

    preview = client.post(f"{BASE}/attachments/preview", json=selection)

    assert preview.status_code == 200
    assert preview.json()["total"] == 2 and preview.json()["capped"] is False
    uploaded = client.post(
        f"{BASE}/attachments",
        files=[("files", PNG), ("files", ("otro.png", b"\x89PNG 2", "image/png"))],
        data={"selection": json.dumps(selection)},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["supports"] == 2 and uploaded.json()["links_created"] == 4
    for support in supports:
        assert len(client.get(f"{BASE}/{support}/attachments").json()) == 2


def test_documents_already_in_the_folder_are_linked_by_id(client, supports) -> None:
    first = client.post(f"{BASE}/{supports[0]}/attachments", files=[("files", PNG)]).json()

    linked = client.post(
        f"{BASE}/attachments",
        data={
            "selection": json.dumps({"support_ids": supports}),
            "document_ids": json.dumps(first["documents"]),
        },
    )

    assert linked.status_code == 201 and linked.json()["links_created"] == 1
    assert client.get(f"{BASE}/{supports[1]}/attachments").json()[0]["support_count"] == 2


def test_bad_requests(client, supports) -> None:
    unfiltered = client.post(f"{BASE}/attachments/preview", json={"search": {}})
    assert unfiltered.status_code == 422 and "al menos un filtro" in unfiltered.json()["detail"]
    assert client.post(f"{BASE}/attachments/preview", json={}).status_code == 422
    no_file = client.post(
        f"{BASE}/attachments", data={"selection": json.dumps({"support_ids": supports})}
    )
    assert no_file.status_code == 422 and "al menos un archivo" in no_file.json()["detail"]
    broken = client.post(f"{BASE}/attachments", data={"selection": "{nope"}, files=[("files", PNG)])
    assert broken.status_code == 422 and "JSON" in broken.json()["detail"]
    bad_ids = client.post(
        f"{BASE}/attachments",
        data={"selection": json.dumps({"support_ids": supports}), "document_ids": '["x"]'},
    )
    assert bad_ids.status_code == 422
    assert client.get(f"{BASE}/00000000-0000-0000-0000-000000000000/attachments").status_code == 404


def test_writing_is_for_managers_but_listing_is_open(client, supports) -> None:
    client.app.dependency_overrides[get_current_principal] = lambda: person("otro@jdh.com.co")

    assert (
        client.post(f"{BASE}/{supports[0]}/attachments", files=[("files", PNG)]).status_code == 403
    )
    assert (
        client.post(f"{BASE}/attachments/preview", json={"support_ids": supports}).status_code
        == 403
    )
    assert client.get(f"{BASE}/{supports[0]}/attachments").status_code == 200


def test_fixed_routes_are_not_read_as_support_ids(client) -> None:
    # POST /attachments and /attachments/preview must not land on /{support_id}/...
    assert client.post(f"{BASE}/attachments/preview", json={"support_ids": []}).status_code == 422
    assert client.get(f"{BASE}/attachments").status_code == 422  # GET /{support_id}: not a UUID
