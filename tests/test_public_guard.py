"""The guard of the public forms: size, per-address and global limits, CAPTCHA."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.config import Environment, Settings
from app.main import create_app

URL = "/api/v1/public/pqrsf/requests"
HEADERS = {"Idempotency-Key": "0d9f3c1e-6b1a-4f0e-9d2c-4a7f6a3b1c55"}


class CountingRedis:
    """A sliding window that only counts: enough to tell where a limit is hit."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.down = False

    async def allow_sliding_window(self, bucket, limit, window_seconds):
        if self.down:
            raise RedisConnectionError("redis caído")
        self.counts[bucket] = count = self.counts.get(bucket, 0) + 1
        return SimpleNamespace(allowed=count <= limit, count=count, retry_after=window_seconds)

    async def close(self):
        pass


def _client(redis: CountingRedis, **overrides):
    values = {
        "ENVIRONMENT": Environment.TEST,
        "CORS_ORIGINS": "http://localhost:3000",
        "RATE_LIMIT_TRUSTED_PROXY_IPS": "testclient",
        "RATE_LIMIT_STRICT_REQUESTS": 3,
        "RATE_LIMIT_PUBLIC_GLOBAL_REQUESTS": 100,
        "PUBLIC_MAX_BODY_MB": 1,
    }
    values.update(overrides)
    app = create_app(Settings(**values))
    return app, redis


@pytest.fixture
def redis() -> CountingRedis:
    return CountingRedis()


def _post(client, ip="10.0.0.1", **kwargs):
    headers = {**HEADERS, "X-Forwarded-For": ip, **kwargs.pop("headers", {})}
    # An empty form is rejected by validation (422): reaching it means the guard let it pass.
    return client.post(URL, data=kwargs.pop("data", {}), headers=headers, **kwargs)


def test_a_request_within_the_limits_reaches_the_form(redis) -> None:
    app, _ = _client(redis)
    with app.state.container.redis.override(redis), TestClient(app) as client:
        assert _post(client).status_code == 422


def test_a_body_over_the_cap_is_refused_before_it_is_read(redis) -> None:
    app, _ = _client(redis)
    with app.state.container.redis.override(redis), TestClient(app) as client:
        big = _post(client, content=b"x" * (1024 * 1024 + 1))

    assert big.status_code == 413 and "1 MB" in big.json()["detail"]
    assert redis.counts == {}  # nothing was even counted: it never got that far


def test_a_body_without_a_declared_size_is_refused(redis) -> None:
    app, _ = _client(redis)

    def chunks():
        yield b"a=1"

    with app.state.container.redis.override(redis), TestClient(app) as client:
        assert _post(client, content=chunks()).status_code == 411


def test_one_address_is_limited_and_told_when_to_return(redis) -> None:
    app, _ = _client(redis)
    with app.state.container.redis.override(redis), TestClient(app) as client:
        statuses = [_post(client).status_code for _ in range(4)]
        limited = _post(client)
        other = _post(client, ip="10.0.0.2")

    assert statuses == [422, 422, 422, 429]
    assert limited.status_code == 429 and limited.headers["Retry-After"] == "600"
    assert limited.headers["X-RateLimit-Remaining"] == "0"
    assert other.status_code == 422  # another visitor is not affected


def test_a_flood_spread_over_many_addresses_hits_the_global_limit(redis) -> None:
    app, _ = _client(redis, RATE_LIMIT_PUBLIC_GLOBAL_REQUESTS=3)
    with app.state.container.redis.override(redis), TestClient(app) as client:
        statuses = [_post(client, ip=f"10.0.0.{n}").status_code for n in range(1, 6)]

    assert statuses == [422, 422, 422, 429, 429]


def test_limit_answers_carry_the_cors_headers_the_browser_needs(redis) -> None:
    app, _ = _client(redis, RATE_LIMIT_STRICT_REQUESTS=1)
    with app.state.container.redis.override(redis), TestClient(app) as client:
        _post(client)
        limited = _post(client, headers={"Origin": "http://localhost:3000"})

    assert limited.status_code == 429
    assert limited.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_without_redis_nothing_is_admitted(redis) -> None:
    redis.down = True
    app, _ = _client(redis)
    with app.state.container.redis.override(redis), TestClient(app) as client:
        assert _post(client).status_code == 503


def test_reads_are_not_counted_by_the_form_guard(redis) -> None:
    app, _ = _client(redis)
    with app.state.container.redis.override(redis), TestClient(app) as client:
        assert client.get(URL).status_code == 405  # reads are the catalog's concern

    assert not any(bucket.startswith(("strict", "public_global")) for bucket in redis.counts)


# --- CAPTCHA ---


class Turnstile:
    """Stands in for Cloudflare: answers the verification the guard asks for."""

    def __init__(self, answer) -> None:
        self.answer = answer
        self.asked: list[dict] = []

    def install(self, monkeypatch) -> None:
        outer = self

        class Client:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, data=None):
                outer.asked.append({"url": url, **(data or {})})
                if isinstance(outer.answer, Exception):
                    raise outer.answer
                return httpx.Response(200, json=outer.answer, request=httpx.Request("POST", url))

        monkeypatch.setattr("app.core.public_guard.httpx.AsyncClient", Client)


def _captcha_client(redis):
    return _client(redis, PUBLIC_CAPTCHA_SECRET="turnstile-secret")[0]


def test_the_captcha_is_off_unless_a_secret_is_set(redis) -> None:
    app, _ = _client(redis)
    with app.state.container.redis.override(redis), TestClient(app) as client:
        assert _post(client).status_code == 422  # no token needed


def test_a_missing_or_rejected_captcha_token_is_refused(redis, monkeypatch) -> None:
    turnstile = Turnstile({"success": False})
    turnstile.install(monkeypatch)
    app = _captcha_client(redis)
    with app.state.container.redis.override(redis), TestClient(app) as client:
        missing = _post(client)
        rejected = _post(client, headers={"X-Captcha-Token": "bad"})

    assert missing.status_code == 403 and rejected.status_code == 403
    assert len(turnstile.asked) == 1  # no call is made for a request with no token


def test_a_valid_captcha_token_lets_the_request_through(redis, monkeypatch) -> None:
    turnstile = Turnstile({"success": True})
    turnstile.install(monkeypatch)
    app = _captcha_client(redis)
    with app.state.container.redis.override(redis), TestClient(app) as client:
        response = _post(client, headers={"X-Captcha-Token": "good"})

    assert response.status_code == 422
    (asked,) = turnstile.asked
    assert (asked["secret"], asked["response"], asked["remoteip"]) == (
        "turnstile-secret",
        "good",
        "10.0.0.1",
    )


def test_when_the_captcha_service_is_down_the_request_is_not_admitted(redis, monkeypatch) -> None:
    Turnstile(httpx.ConnectError("sin red")).install(monkeypatch)
    app = _captcha_client(redis)
    with app.state.container.redis.override(redis), TestClient(app) as client:
        response = _post(client, headers={"X-Captcha-Token": "good"})

    assert response.status_code == 503
