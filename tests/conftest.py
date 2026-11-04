import pytest

from app.infrastructure.redis import RateLimitDecision, RedisClient
from app.modules.areas.presentation import dependencies as permission_gates


@pytest.fixture(autouse=True)
def _no_dev_bypass_from_local_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's .env may enable the dev bypass; tests build test/prod settings.

    Environment variables win over .env in pydantic-settings, so this keeps
    ``Settings(ENVIRONMENT=...)`` valid regardless of the local file.
    """

    monkeypatch.setenv("AUTH_DEV_BYPASS", "false")


@pytest.fixture(autouse=True)
def _traffic_limiter_without_redis(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every endpoint is rate limited, so tests that go through HTTP need a counter.

    This one admits everything. Tests of the limiter itself replace the container's Redis
    with their own counter, which takes precedence; a module that tests the real counter
    sets ``REAL_REDIS_LIMITER = True``.
    """

    if getattr(request.module, "REAL_REDIS_LIMITER", False):
        return

    async def admit(self, bucket: str, limit: int, window_seconds: int) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, count=1)

    monkeypatch.setattr(RedisClient, "allow_sliding_window", admit)


class _OpenGates:
    """Lets every router read gate pass; the services still apply their own policies."""

    async def allows(self, principal, area_slug, module, action="read") -> bool:
        return True

    async def require_permission(self, principal, area_slug, module, action="read") -> None:
        return None


@pytest.fixture(autouse=True)
def _module_gates_without_database(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Router read gates resolve the real area service, which needs PostgreSQL.

    HTTP tests of a feature replace the feature's service, not the areas; this opens the
    gates so they keep testing the feature's own rules. A module that tests the gates
    sets ``REAL_PERMISSION_GATES = True`` and overrides the container's ``area_service``.
    """

    if getattr(request.module, "REAL_PERMISSION_GATES", False):
        return
    monkeypatch.setattr(permission_gates, "_areas", lambda request: _OpenGates())
