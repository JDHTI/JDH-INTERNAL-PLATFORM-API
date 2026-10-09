"""HTTP contract of the commercial master routes."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.commercial_masters.application.service import CommercialMasterService
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from fastapi.testclient import TestClient

from tests.modules.commercial_masters.test_service import Managers, Repo

BASE = "/api/v1/commercial"


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
    service = CommercialMasterService(repo, Managers())
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
        params={"nit": "800.116.299", "channel": "RESTAURANTES", "is_active": "true", "size": 5},
    )

    assert response.status_code == 200
    assert repo.calls[-1][:4] == ("customers", None, "800116299", "RESTAURANTES")


def test_patch_sends_only_the_fields_present_in_the_body(api) -> None:
    client, repo = api

    response = client.patch(f"{BASE}/suppliers/233", json={"list_margin_notes": "Aseo 20%"})

    assert response.status_code == 200
    assert repo.calls[-1][2] == {"list_margin_notes": "Aseo 20%"}


def test_patch_rejects_out_of_range_margins_and_non_managers(api, outsider_api) -> None:
    client, _ = api
    other, _ = outsider_api

    assert client.patch(f"{BASE}/suppliers/233", json={"list_margin_pct": 18}).status_code == 422
    assert other.patch(f"{BASE}/suppliers/233", json={"list_margin_pct": 0.18}).status_code == 403
    assert other.get(f"{BASE}/suppliers/233").status_code == 200  # reading is open
