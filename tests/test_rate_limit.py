import pytest

from app.core.config import Settings
from app.infrastructure.redis import RedisClient

# This module tests the real counter: the conftest must not replace it with an admit-all.
REAL_REDIS_LIMITER = True


def test_rate_limit_profiles_are_configurable() -> None:
    settings = Settings(
        RATE_LIMIT_STRICT_REQUESTS=2,
        RATE_LIMIT_STRICT_WINDOW_SECONDS=30,
        RATE_LIMIT_TRUSTED_PROXY_IPS="10.0.0.1, 10.0.0.2",
    )

    assert settings.rate_limit_policy("strict") == (2, 30)
    assert settings.trusted_proxy_ips == frozenset({"10.0.0.1", "10.0.0.2"})


def test_unknown_rate_limit_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown rate limit profile"):
        Settings().rate_limit_policy("burst")


@pytest.mark.asyncio
async def test_redis_rate_limit_uses_prefixed_sliding_window_keys() -> None:
    calls: list[tuple[object, ...]] = []

    class FakeRedis:
        async def eval(self, *args: object) -> list[int]:
            calls.append(args)
            return [1, 3, 0]

    redis = RedisClient.__new__(RedisClient)
    redis.key_prefix = "internal-platform"
    redis.client = FakeRedis()

    decision = await redis.allow_sliding_window("strict:client", 5, 600)

    assert decision.allowed is True
    assert decision.count == 3
    assert calls[0][1:4] == (
        2,
        "internal-platform:rate-limit:strict:client",
        "internal-platform:rate-limit:strict:client:sequence",
    )
