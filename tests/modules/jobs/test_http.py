"""HTTP contract of ``/jobs``."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.identity.presentation.router import get_current_user

BASE = "/api/v1/jobs"


class AllowAll:
    async def allow_sliding_window(self, bucket, limit, window):
        return SimpleNamespace(allowed=True, count=1, retry_after=0)

    async def close(self):
        return None


def user(role: str = "user") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        email=f"{role}@jdh.com.co",
        name=role.title(),
        is_active=True,
        role=SimpleNamespace(code=role),
    )


@pytest.fixture
def harness(make_service):
    return SimpleNamespace(service=make_service(), caller=user("moderator"))


@pytest.fixture
def client(harness):
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[get_current_user] = lambda: harness.caller
    v1 = app.state.container.v1_container
    with (
        app.state.container.redis.override(AllowAll()),
        v1.job_service.override(harness.service),
        TestClient(app) as test_client,
    ):
        yield test_client
    app.dependency_overrides.clear()


def test_a_moderator_reads_the_jobs_but_cannot_run_one(client, harness) -> None:
    asyncio.run(harness.service.poll_dwh())

    body = client.get(BASE).json()
    runs = client.get(f"{BASE}/runs", params={"job": "masters-warm-identity"}).json()

    assert body["enabled"] is True and body["fallback_at"] == "10:00"
    assert body["dwh"]["error"] is None and body["dwh"]["last_poll_at"]
    identity = next(item for item in body["items"] if item["key"] == "masters-warm-identity")
    assert identity["sequence"] == "identidad"
    assert identity["last_run"]["status"] == "queued"
    assert identity["last_run"]["trigger"] == "dwh"
    assert identity["next_expected_at"] is None
    assert identity["sequence_state"]["ultima_estado"] == "success"
    assert [run["job"] for run in runs] == ["masters-warm-identity"]
    assert client.post(f"{BASE}/masters-warm-identity/run").status_code == 403


def test_an_admin_runs_a_job_once(client, harness) -> None:
    harness.caller = user("admin")

    first = client.post(f"{BASE}/masters-warm-products/run")
    again = client.post(f"{BASE}/masters-warm-products/run")

    assert first.status_code == 202 and "run_id" in first.json()
    assert again.status_code == 409
    assert client.post(f"{BASE}/nada/run").status_code == 404
    assert client.get(f"{BASE}/runs", params={"job": "nada"}).status_code == 404


def test_a_plain_user_sees_nothing(client, harness) -> None:
    harness.caller = user()

    assert client.get(BASE).status_code == 403
    assert client.get(f"{BASE}/runs").status_code == 403
