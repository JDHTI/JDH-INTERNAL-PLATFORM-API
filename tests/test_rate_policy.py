"""Rate-limit levels: which endpoint has which, and that the middleware applies them."""

from pathlib import Path

import pytest
from app.core.config import Environment, Settings
from app.core.rate_policy import LEVELS, READ, RELAXED, RULES, STRICT, RatePolicy, describe
from app.main import create_app
from fastapi.testclient import TestClient

from tests.test_public_guard import CountingRedis

PREFIX = "/api"
DOC = Path(__file__).parent.parent / "docs" / "rate-limiting.md"
TABLE_START, TABLE_END = "<!-- niveles:inicio -->", "<!-- niveles:fin -->"


def _settings(**overrides) -> Settings:
    values = {
        "ENVIRONMENT": Environment.TEST,
        "CORS_ORIGINS": "http://localhost:3000",
        "RATE_LIMIT_TRUSTED_PROXY_IPS": "testclient",
    }
    values.update(overrides)
    return Settings(**values)


def _operations(app) -> list[tuple[str, str]]:
    spec = app.openapi()
    return [
        (method.upper(), path)
        for path, operations in spec["paths"].items()
        for method in operations
    ]


# --- The policy ---


def test_the_level_follows_the_method_by_default() -> None:
    policy = RatePolicy(PREFIX)

    assert policy.level_for("GET", "/api/v1/sites") == READ
    assert policy.level_for("HEAD", "/api/v1/sites") == READ
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert policy.level_for(method, "/api/v1/sites/abc") == RELAXED
    # What nobody listed is limited anyway: an unknown path is a read, never free.
    assert policy.level_for("GET", "/api/v1/algo-nuevo") == READ


def test_the_exceptions_name_each_endpoint_and_variable_segments_match_any_value() -> None:
    policy = RatePolicy(PREFIX)

    assert policy.level_for("GET", "/api/v1/pqrsf/cases/export") == STRICT
    assert policy.level_for("GET", "/api/v1/pqrsf/cases/export/") == STRICT
    assert policy.level_for("POST", "/api/v1/public/pqrsf/requests") == STRICT
    # A case literally called "export" is the export; any other id is an ordinary read.
    assert policy.level_for("GET", "/api/v1/pqrsf/cases/8f2e") == READ
    # The rule is for that method only.
    assert policy.level_for("POST", "/api/v1/pqrsf/cases/export") == RELAXED


def test_probes_and_preflight_are_not_limited() -> None:
    policy = RatePolicy(PREFIX)

    assert policy.level_for("GET", "/api/health/live") is None
    assert policy.level_for("GET", "/api/health/ready") is None
    assert policy.level_for("OPTIONS", "/api/v1/pqrsf/cases") is None


def test_every_endpoint_of_the_api_has_a_level_and_only_the_listed_ones_are_exceptions() -> None:
    app = create_app(_settings())
    policy = RatePolicy(PREFIX)
    operations = _operations(app)
    assert len(operations) > 100  # the whole API, not a sample

    exceptions = {(rule.method, rule.path) for rule in RULES}
    seen = set()
    for method, path in operations:
        level = policy.level_for(method, path)
        relative = path.removeprefix(PREFIX)
        rule = policy.rule_for(method, path)
        if rule is not None:
            seen.add((rule.method, rule.path))
            continue
        assert level in LEVELS, f"{method} {path} has no level"
        # Without an exception a query is a read and a change is relaxed.
        assert level == (READ if method == "GET" else RELAXED), f"{method} {relative}"
    # An exception for a route that no longer exists is a mistake waiting to happen.
    assert seen == exceptions


def test_no_change_is_ever_a_read() -> None:
    policy = RatePolicy(PREFIX)
    app = create_app(_settings())

    for method, path in _operations(app):
        if method != "GET":
            assert policy.level_for(method, path) != READ, f"{method} {path}"


def test_the_document_lists_the_level_of_every_endpoint() -> None:
    app = create_app(_settings())
    text = DOC.read_text(encoding="utf-8")
    start, end = text.index(TABLE_START) + len(TABLE_START), text.index(TABLE_END)

    assert text[start:end].strip() == describe(PREFIX, _operations(app)).strip(), (
        "docs/rate-limiting.md is out of date: run `python -m app.core.rate_policy --write`"
    )


# --- The middleware ---


@pytest.fixture
def redis() -> CountingRedis:
    return CountingRedis()


def _get(client, path="/api/v1/auth/me", ip="10.0.0.1", **kwargs):
    return client.get(path, headers={"X-Forwarded-For": ip, **kwargs.pop("headers", {})}, **kwargs)


def _post(client, path="/api/v1/auth/logout", ip="10.0.0.1", **kwargs):
    return client.post(path, headers={"X-Forwarded-For": ip, **kwargs.pop("headers", {})}, **kwargs)


def test_each_level_counts_apart_and_says_which_one_applied(redis) -> None:
    app = create_app(_settings(RATE_LIMIT_READ_REQUESTS=3, RATE_LIMIT_RELAXED_REQUESTS=2))
    with app.state.container.redis.override(redis), TestClient(app) as client:
        reads = [_get(client) for _ in range(4)]
        # The reads used up their budget; the changes still have theirs.
        writes = [_post(client) for _ in range(3)]

    assert [r.status_code == 429 for r in reads] == [False, False, False, True]
    assert reads[0].headers["X-RateLimit-Policy"] == "read"
    assert reads[0].headers["X-RateLimit-Limit"] == "3"
    assert reads[0].headers["X-RateLimit-Remaining"] == "2"
    assert [w.status_code == 429 for w in writes] == [False, False, True]
    assert writes[0].headers["X-RateLimit-Policy"] == "relaxed"


def test_a_refused_request_says_when_to_come_back_and_stays_readable_by_the_browser(
    redis,
) -> None:
    app = create_app(_settings(RATE_LIMIT_READ_REQUESTS=1))
    with app.state.container.redis.override(redis), TestClient(app) as client:
        _get(client)
        refused = _get(client, headers={"Origin": "http://localhost:3000"})

    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "60"
    assert refused.headers["X-RateLimit-Remaining"] == "0"
    assert refused.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "límite" in refused.json()["detail"]


def test_one_address_does_not_use_up_the_budget_of_another(redis) -> None:
    app = create_app(_settings(RATE_LIMIT_READ_REQUESTS=1))
    with app.state.container.redis.override(redis), TestClient(app) as client:
        first = [_get(client, ip="10.0.0.1").status_code for _ in range(2)]
        other = _get(client, ip="10.0.0.2").status_code

    assert 429 in first and other != 429


def test_the_export_is_strict(redis) -> None:
    app = create_app(_settings(RATE_LIMIT_STRICT_REQUESTS=1))
    with app.state.container.redis.override(redis), TestClient(app) as client:
        first = _get(client, "/api/v1/pqrsf/cases/export")
        second = _get(client, "/api/v1/pqrsf/cases/export")

    assert first.headers["X-RateLimit-Policy"] == "strict" and first.status_code != 429
    assert second.status_code == 429


def test_probes_are_never_counted_or_refused(redis) -> None:
    app = create_app(_settings(RATE_LIMIT_READ_REQUESTS=1))
    with app.state.container.redis.override(redis), TestClient(app) as client:
        results = [_get(client, "/api/health/live") for _ in range(5)]

    assert all(r.status_code == 200 for r in results)
    assert redis.counts == {}
    assert "X-RateLimit-Policy" not in results[0].headers


def test_the_public_form_is_counted_once_not_by_both_guards(redis) -> None:
    app = create_app(_settings(RATE_LIMIT_STRICT_REQUESTS=3, PUBLIC_MAX_BODY_MB=1))
    with app.state.container.redis.override(redis), TestClient(app) as client:
        client.post(
            "/api/v1/public/pqrsf/requests",
            data={},
            headers={"Idempotency-Key": "0d9f3c1e-6b1a-4f0e-9d2c-4a7f6a3b1c55"},
        )

    strict = [count for bucket, count in redis.counts.items() if bucket.startswith("strict:")]
    assert strict == [1]
    assert not any(bucket.startswith(("read:", "relaxed:")) for bucket in redis.counts)


def test_without_redis_nothing_is_admitted_but_probes_still_answer(redis) -> None:
    redis.down = True
    app = create_app(_settings())
    with app.state.container.redis.override(redis), TestClient(app) as client:
        assert _get(client).status_code == 503
        assert _get(client, "/api/health/live").status_code == 200
