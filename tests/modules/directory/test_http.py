"""HTTP contract of the directory routes."""

from __future__ import annotations

import asyncio
from datetime import date, time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.directory.application.service import DirectoryService
from app.modules.directory.domain.entities import Visibility
from app.modules.identity.presentation.router import get_current_user
from tests.modules.directory.conftest import NOW

BASE = "/api/v1/directory"


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


class Harness:
    def __init__(self, repository, runs, storage) -> None:
        self.repository = repository
        self.runs = runs
        self.storage = storage
        self.requested: list = []
        self.service = DirectoryService(
            repository,
            runs,
            storage,
            sync_enabled=True,
            sync_at=time(23, 59),
            timezone="America/Bogota",
            request_sync=self.requested.append,
            clock=lambda: NOW,
        )
        self.caller = user()

    def as_admin(self) -> None:
        self.caller = user("admin")


@pytest.fixture
def harness(repository, runs, storage):
    return Harness(repository, runs, storage)


@pytest.fixture
def client(harness):
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[get_current_user] = lambda: harness.caller
    v1 = app.state.container.v1_container
    with (
        app.state.container.redis.override(AllowAll()),
        v1.directory_service.override(harness.service),
        TestClient(app) as test_client,
    ):
        yield test_client
    app.dependency_overrides.clear()


async def _seed(harness: Harness):
    repo = harness.repository
    site = repo.add_site("Bodega Principal", "Calle 1")
    ana = await repo.create_employee(
        {
            "full_name": "Ana María Pérez",
            "email": "ana@jdh.com.co",
            "position": "Analista BI",
            "department": "BI",
            "site_id": site.id,
            "birth_date": date(1990, 11, 20),
            "hire_date": date(2026, 10, 1),
            "user_id": harness.caller.id,
            "entra_object_id": "ana",
            "photo_key": "directory-photos/ana/432",
            "photo_thumb_key": "directory-photos/ana/96",
            "photo_content_type": "image/jpeg",
            "photo_updated_at": NOW,
        }
    )
    luis = await repo.create_employee(
        {"full_name": "Luis Gómez", "department": "BI", "birth_date": date(1985, 11, 2)}
    )
    hidden = await repo.create_employee(
        {"full_name": "Buzón Compartido", "department": "BI", "visibility": Visibility.HIDDEN}
    )
    await harness.storage.put_object("directory-photos/ana/96", b"small", "image/jpeg")
    await harness.storage.put_object("directory-photos/ana/432", b"large", "image/jpeg")
    return site, ana, luis, hidden


def test_the_public_directory_lists_searches_and_hides(client, harness) -> None:
    site, ana, luis, hidden = asyncio.run(_seed(harness))

    listed = client.get(f"{BASE}/employees").json()
    assert listed["total"] == 2 and [i["full_name"] for i in listed["items"]] == [
        "Ana María Pérez",
        "Luis Gómez",
    ]
    assert listed["items"][0]["site"] == {"id": str(site.id), "name": "Bodega Principal"}
    assert listed["items"][0]["has_photo"] is True and listed["items"][1]["has_photo"] is False

    found = client.get(f"{BASE}/employees", params={"q": "perez ana"}).json()
    assert [i["full_name"] for i in found["items"]] == ["Ana María Pérez"]
    assert client.get(f"{BASE}/employees", params={"size": 101}).status_code == 422

    assert client.get(f"{BASE}/employees/{hidden.id}").status_code == 404
    profile = client.get(f"{BASE}/employees/{ana.id}").json()
    assert profile["birth_month"] == 11 and profile["birth_day"] == 20
    assert "birth_date" not in profile

    me = client.get(f"{BASE}/employees/me")
    assert me.status_code == 200 and me.json()["id"] == str(ana.id)

    team = client.get(f"{BASE}/employees/{ana.id}/team").json()["items"]
    assert [i["full_name"] for i in team] == ["Luis Gómez"]

    sites = client.get(f"{BASE}/sites").json()["items"]
    assert sites[0]["employee_count"] == 1


def test_birthdays_and_new_hires(client, harness) -> None:
    asyncio.run(_seed(harness))

    birthdays = client.get(f"{BASE}/employees/birthdays").json()
    assert birthdays["month"] == 11
    assert [(i["full_name"], i["birth_day"]) for i in birthdays["items"]] == [
        ("Luis Gómez", 2),
        ("Ana María Pérez", 20),
    ]
    assert client.get(f"{BASE}/employees/birthdays", params={"month": 13}).status_code == 422

    hires = client.get(f"{BASE}/employees/new-hires").json()["items"]
    assert [(i["full_name"], i["hire_date"]) for i in hires] == [("Ana María Pérez", "2026-10-01")]


def test_photos_are_cached_by_version(client, harness) -> None:
    _, ana, luis, _ = asyncio.run(_seed(harness))
    version = client.get(f"{BASE}/employees/{ana.id}").json()["photo_version"]

    small = client.get(f"{BASE}/employees/{ana.id}/photo", params={"v": version})
    assert small.status_code == 200 and small.content == b"small"
    assert small.headers["content-type"] == "image/jpeg"
    assert "immutable" in small.headers["cache-control"]

    large = client.get(f"{BASE}/employees/{ana.id}/photo", params={"size": 432})
    assert large.content == b"large" and "immutable" not in large.headers["cache-control"]

    cached = client.get(
        f"{BASE}/employees/{ana.id}/photo", headers={"If-None-Match": small.headers["etag"]}
    )
    assert cached.status_code == 304 and cached.content == b""

    assert client.get(f"{BASE}/employees/{luis.id}/photo").status_code == 404
    assert client.get(f"{BASE}/employees/{ana.id}/photo", params={"size": 200}).status_code == 422


def test_me_is_404_until_the_sync(client) -> None:
    assert client.get(f"{BASE}/employees/me").status_code == 404


def test_administration_needs_the_admin_role(client) -> None:
    assert client.get(f"{BASE}/admin/employees").status_code == 403
    assert client.post(f"{BASE}/admin/sync").status_code == 403


def test_admins_edit_manual_and_synced_employees(client, harness) -> None:
    site, ana, _, hidden = asyncio.run(_seed(harness))
    harness.as_admin()

    everyone = client.get(f"{BASE}/admin/employees").json()
    assert everyone["total"] == 3
    hidden_row = next(i for i in everyone["items"] if i["id"] == str(hidden.id))
    assert hidden_row["is_public"] is False and hidden_row["visibility"] == "hidden"

    created = client.post(
        f"{BASE}/admin/employees",
        json={"full_name": "  Contratista   Externo ", "site_id": str(site.id)},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["full_name"] == "Contratista Externo" and body["is_synced"] is False

    patched = client.patch(
        f"{BASE}/admin/employees/{ana.id}",
        json={"full_name": "Otra", "position": "Líder BI", "visibility": "hidden"},
    ).json()
    assert patched["full_name"] == "Ana María Pérez"
    assert patched["position"] == "Líder BI" and patched["visibility"] == "hidden"

    bad_site = client.patch(f"{BASE}/admin/employees/{ana.id}", json={"site_id": str(uuid4())})
    assert bad_site.status_code == 422

    assert client.delete(f"{BASE}/admin/employees/{ana.id}").status_code == 409
    assert client.delete(f"{BASE}/admin/employees/{body['id']}").status_code == 204


def test_admins_manage_sites(client, harness) -> None:
    site, _, _, _ = asyncio.run(_seed(harness))
    harness.as_admin()

    created = client.post(f"{BASE}/admin/sites", json={"name": "Sede Norte"})
    assert created.status_code == 201
    assert client.post(f"{BASE}/admin/sites", json={"name": "sede norte"}).status_code == 409
    renamed = client.patch(
        f"{BASE}/admin/sites/{created.json()['id']}", json={"address": "Carrera 7"}
    )
    assert renamed.json()["address"] == "Carrera 7"
    assert client.delete(f"{BASE}/admin/sites/{site.id}").status_code == 409
    assert client.delete(f"{BASE}/admin/sites/{created.json()['id']}").status_code == 204


def test_admins_request_a_sync_and_read_its_status(client, harness) -> None:
    harness.as_admin()

    requested = client.post(f"{BASE}/admin/sync")
    assert requested.status_code == 202
    run_id = requested.json()["run_id"]
    assert [str(i) for i in harness.requested] == [run_id]
    assert client.post(f"{BASE}/admin/sync").status_code == 409

    state = client.get(f"{BASE}/admin/sync").json()
    assert state["enabled"] is True and state["running"] is True
    assert state["scheduled_at"] == "23:59" and state["latest"]["id"] == run_id
    assert state["next_run_at"].startswith("2026-11-05T04:59")

    history = client.get(f"{BASE}/admin/sync/runs").json()["items"]
    assert history[0]["triggered_by"] == "admin@jdh.com.co"
    assert history[0]["trigger"] == "manual"


def test_a_sync_cannot_be_requested_while_it_is_switched_off(client, harness) -> None:
    harness.as_admin()
    harness.service.sync_enabled = False

    assert client.post(f"{BASE}/admin/sync").status_code == 409
