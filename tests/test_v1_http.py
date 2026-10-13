"""HTTP contract of the areas and documents modules, services overridden in the container."""

import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.areas.application.dto import AreaView
from app.modules.identity.presentation.router import get_current_user, require_admin
from tests.modules.fakes import NOW


def test_areas_and_documents_require_a_session() -> None:
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    with TestClient(app) as client:
        for path in ("/api/v1/areas", "/api/v1/areas/ti", "/api/v1/documents/search"):
            response = client.get(path)
            assert response.status_code == 401, path


def test_areas_see_the_session_user_as_principal() -> None:

    session_user = SimpleNamespace(
        id=uuid.uuid4(),
        email="analista.ti@jdh.com.co",
        name="Analista TI",
        role=SimpleNamespace(code="admin"),
    )
    seen = []

    class StubAreas:
        async def list_areas(self, principal):
            seen.append(principal)
            return [
                AreaView(
                    id=uuid.uuid4(),
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
    app.dependency_overrides[get_current_user] = lambda: session_user
    container = app.state.container
    with (
        container.v1_container.area_service.override(StubAreas()),
        TestClient(app) as client,
    ):
        response = client.get("/api/v1/areas")

    assert response.status_code == 200
    body = response.json()
    assert [(a["slug"], a["icon"], a["sort_order"]) for a in body] == [("ti", "Server", 10)]
    assert seen[0].user_id == session_user.id
    assert seen[0].email == "analista.ti@jdh.com.co"
    assert seen[0].is_platform_admin is True


def test_admin_user_list_forwards_activity_filter_and_pagination() -> None:

    admin = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id="tenant",
        entra_object_id="admin-object",
        name="Analista TI",
        email="analista.ti@jdh.com.co",
        job_title="Analista TI",
        is_active=True,
        role=SimpleNamespace(code="admin"),
        area_id=None,
        area=None,
        photo_key=None,
        photo_content_type=None,
    )
    listed = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id="tenant",
        entra_object_id="mock-1",
        name="Usuario Mock 1",
        email="mock.usuario1@jdh.com.co",
        job_title="Usuario de pruebas",
        is_active=True,
        role=SimpleNamespace(code="user"),
        area_id=None,
        area=None,
        photo_key=None,
        photo_content_type=None,
    )
    seen: list[tuple[int, int, bool | None]] = []

    class StubIdentity:
        async def list_users(self, offset, limit, is_active=None):
            seen.append((offset, limit, is_active))
            return [listed], 1

    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[require_admin] = lambda: admin
    with app.state.container.v1_container.identity_service.override(StubIdentity()):
        with TestClient(app) as client:
            response = client.get("/api/v1/users?offset=2&limit=10&is_active=true")

    assert response.status_code == 200
    assert seen == [(2, 10, True)]
    assert response.json()["items"][0]["is_active"] is True
    assert response.json()["total"] == 1
