import pytest

from app.infrastructure.redis import RateLimitDecision, RedisClient


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
