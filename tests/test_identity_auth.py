from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.identity.application.service import (
    AuthenticatedUser,
    AuthenticationRequired,
)
from app.modules.identity.infrastructure.entra import TokenSet


def _settings(**kwargs: object) -> Settings:
    return Settings(
        ENVIRONMENT=Environment.DEV,
        ENTRA_TENANT_ID="tenant",
        ENTRA_CLIENT_ID="client",
        ENTRA_API_AUDIENCE="api://client",
        AUTH_COOKIE_SECURE=False,
        **kwargs,
    )


def _user(role: str = "user") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        tenant_id="tenant",
        entra_object_id="object",
        name="Ada Lovelace",
        email="ada@example.test",
        job_title="Engineer",
        is_active=True,
        role=SimpleNamespace(code=role),
        area_id=None,
        area=None,
        photo_key=None,
        photo_content_type=None,
    )


class FakeEntra:
    def authorization_url(self, state: str, challenge: str) -> str:
        return f"https://login.test/authorize?state={state}&challenge={challenge}"


class FakeIdentityService:
    def __init__(self, settings: Settings, user: SimpleNamespace) -> None:
        self.settings = settings
        self.entra = FakeEntra()
        self.user = user

    async def current_user(
        self, access_token: str | None, refresh_token: str | None
    ) -> AuthenticatedUser:
        if access_token or refresh_token:
            raise AuthenticationRequired
        return AuthenticatedUser(self.user)

    async def complete_code_login(self, code: str, code_verifier: str):
        return self.user, TokenSet("access-token", "refresh-token", 3600), True


def test_sso_callback_sets_http_only_cookies_without_exposing_tokens() -> None:
    settings = _settings()
    app = create_app(settings)
    service = FakeIdentityService(settings, _user())

    with app.state.container.v1_container.identity_service.override(service):
        with TestClient(app) as client:
            login = client.get("/api/v1/auth/login", follow_redirects=False)
            assert login.status_code == 307
            state = client.cookies.get(settings.AUTH_STATE_COOKIE_NAME).split(".", 1)[0]

            callback = client.get(
                "/api/v1/auth/callback", params={"code": "authorization-code", "state": state}
            )

    assert callback.status_code == 200
    assert "access-token" not in callback.text
    assert "refresh-token" not in callback.text
    assert "internal_platform_access=access-token" in callback.headers["set-cookie"]
    assert "HttpOnly" in callback.headers["set-cookie"]


def test_development_bypass_does_not_accept_invalid_cookies() -> None:
    settings = _settings(AUTH_DEV_BYPASS=True)
    app = create_app(settings)
    service = FakeIdentityService(settings, _user("admin"))

    with app.state.container.v1_container.identity_service.override(service):
        with TestClient(app) as client:
            client.cookies.set(settings.AUTH_ACCESS_COOKIE_NAME, "invalid")
            response = client.get("/api/v1/auth/login")

    assert response.status_code == 401


def test_inactive_user_cannot_authenticate_even_with_dev_bypass() -> None:
    settings = _settings(AUTH_DEV_BYPASS=True)
    app = create_app(settings)
    inactive = _user("admin")
    inactive.is_active = False
    service = FakeIdentityService(settings, inactive)

    with app.state.container.v1_container.identity_service.override(service):
        with TestClient(app) as client:
            response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
