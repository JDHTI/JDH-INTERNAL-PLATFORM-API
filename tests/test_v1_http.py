"""HTTP contract of the v1 modules with the services overridden in the container."""

from app.core.config import Environment, Settings
from app.main import create_app
from fastapi.testclient import TestClient


def test_v1_endpoints_require_authentication() -> None:
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    with TestClient(app) as client:
        for path in ("/api/v1/me", "/api/v1/areas", "/api/v1/documents/search"):
            response = client.get(path)
            assert response.status_code == 401, path
            assert response.headers["WWW-Authenticate"] == "Bearer"


def test_dev_bypass_header_is_ignored_when_bypass_is_off() -> None:
    app = create_app(Settings(ENVIRONMENT=Environment.DEV, AUTH_DEV_BYPASS=False))
    with TestClient(app) as client:
        response = client.get("/api/v1/me", headers={"X-Dev-User-Email": "analista.ti@jdh.com.co"})
    assert response.status_code == 401


def test_areas_are_listed_through_the_container(monkeypatch) -> None:
    from app.modules.areas.application.dto import AreaView
    from app.modules.identity.domain.entities import Principal

    from tests.modules.fakes import NOW, principal

    caller: Principal = principal(admin=True)

    class StubIdentity:
        async def authenticate(self, token, dev_email=None):
            return caller

    class StubAreas:
        async def list_areas(self, _principal):
            return [
                AreaView(
                    id=caller.user_id,
                    slug="ti",
                    name="TI",
                    remit="Infraestructura",
                    icon="Server",
                    sort_order=10,
                    responsible=None,
                    my_role=None,
                    is_responsible=False,
                    created_at=NOW,
                    updated_at=NOW,
                )
            ]

    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    container = app.state.container
    with (
        container.v1_container.identity_service.override(StubIdentity()),
        container.v1_container.area_service.override(StubAreas()),
        TestClient(app) as client,
    ):
        response = client.get("/api/v1/areas", headers={"Authorization": "Bearer x"})

    assert response.status_code == 200
    body = response.json()
    assert [(a["slug"], a["icon"], a["sort_order"]) for a in body] == [("ti", "Server", 10)]
