"""HTTP contract of the areas and documents modules, services overridden in the container."""

import uuid
from types import SimpleNamespace

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.identity.presentation.router import get_current_user
from fastapi.testclient import TestClient


def test_areas_and_documents_require_a_session() -> None:
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    with TestClient(app) as client:
        for path in ("/api/v1/areas", "/api/v1/areas/ti", "/api/v1/documents/search"):
            response = client.get(path)
            assert response.status_code == 401, path


def test_areas_see_the_session_user_as_principal() -> None:
    from app.modules.areas.application.dto import AreaView

    from tests.modules.fakes import NOW

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
