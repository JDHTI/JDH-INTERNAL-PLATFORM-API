"""WebSocket contract of the project live channel, services overridden in the container."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import Environment, Settings
from app.core.errors import NotFoundError
from app.main import create_app
from app.modules.identity.application.service import AuthenticationRequired
from app.modules.projects.application.live import ProjectLiveService
from tests.modules.projects.conftest import FakeLiveChannel

AREA = SimpleNamespace(id=uuid.uuid4(), slug="ti", responsible_user_id=None)
USER = SimpleNamespace(
    id=uuid.uuid4(),
    email="ana@jdh.com.co",
    name="Ana",
    is_active=True,
    role=SimpleNamespace(code="user"),
)


class StubIdentity:
    def __init__(self, settings) -> None:
        self.settings = settings

    async def current_user(self, access_token, refresh_token):
        if access_token != "valid":
            raise AuthenticationRequired
        return SimpleNamespace(user=USER, refreshed_tokens=None)


class StubProjects:
    async def authorize(self, slug, principal):

        if slug != "ti":
            raise NotFoundError("Proyectos no está habilitado para esta área")
        return AREA


@pytest.fixture
def settings() -> Settings:
    return Settings(ENVIRONMENT=Environment.TEST, CORS_ORIGINS="http://localhost:3000")


@pytest.fixture
def client(settings):
    app = create_app(settings)
    container = app.state.container.v1_container
    live = ProjectLiveService(channel=FakeLiveChannel(), presence_ttl=45)
    with (
        container.identity_service.override(StubIdentity(settings)),
        container.project_service.override(StubProjects()),
        container.project_live_service.override(live),
        TestClient(app) as test_client,
    ):
        test_client.cookie_name = settings.AUTH_ACCESS_COOKIE_NAME
        yield test_client


def _connect(client, slug="ti", token="valid", origin="http://localhost:3000"):
    client.cookies.set(client.cookie_name, token)
    return client.websocket_connect(
        f"/api/v1/areas/{slug}/projects/live", headers={"origin": origin}
    )


def test_live_greets_and_echoes_presence_changes(client) -> None:
    folder_id = str(uuid.uuid4())
    with _connect(client) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["area_id"] == str(AREA.id)
        assert [m["email"] for m in hello["members"]] == ["ana@jdh.com.co"]
        assert ws.receive_json()["type"] == "presence"  # own arrival

        ws.send_json({"type": "focus", "folder_id": folder_id, "project_id": None})
        presence = ws.receive_json()
        assert presence["type"] == "presence"
        assert presence["members"][0]["folder_id"] == folder_id

        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [({"token": "expired"}, 4401), ({"slug": "bi"}, 4404), ({"origin": "https://evil.test"}, 4403)],
)
def test_live_refusals_carry_a_close_code(client, kwargs, code) -> None:
    with pytest.raises(WebSocketDisconnect) as refused:
        with _connect(client, **kwargs) as ws:
            ws.receive_json()
    assert refused.value.code == code
