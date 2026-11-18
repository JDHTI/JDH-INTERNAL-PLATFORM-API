"""HTTP contract of the action plans: permissions, catalog, an action and its rows, export."""

import io

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.modules.action_plans.fakes import PROCESSES, World
from tests.modules.pqrsf.test_comments_http import OpenRedis
from tests.modules.projects.test_live_http import USER, StubIdentity, settings  # noqa: F401


@pytest.fixture
def world():
    return World()


@pytest.fixture
def client(settings, world):  # noqa: F811
    app = create_app(settings)
    container = app.state.container.v1_container
    with (
        app.state.container.redis.override(OpenRedis()),
        container.identity_service.override(StubIdentity(settings)),
        container.action_plan_service.override(world.service),
        TestClient(app) as test_client,
    ):
        test_client.cookies.set(settings.AUTH_ACCESS_COOKIE_NAME, "valid")
        yield test_client


def _manage(world: World, client) -> None:
    """The stub identity signs in a platform user: make it Calidad."""

    world.policy.managers.add(USER.id)


def test_without_the_module_the_permissions_say_no_and_writes_are_forbidden(client) -> None:
    assert client.get("/api/v1/action-plans/permissions").json() == {
        "can_read": False,
        "can_manage": False,
    }
    payload = {"origin": "AE", "process_id": str(PROCESSES["ALM"].id), "action_type": "mejora"}
    assert client.post("/api/v1/action-plans", json=payload).status_code == 403
    assert client.get("/api/v1/action-plans").json()["total"] == 0
    assert client.get("/api/v1/action-plans/export").status_code == 403


def test_the_catalog_lists_the_choices_of_the_form(client) -> None:
    body = client.get("/api/v1/action-plans/catalog").json()

    assert {o["value"] for o in body["origins"]} == {"AI", "AE", "AT", "IN", "OM", "PI", "SNC"}
    assert [m["value"] for m in body["methods"]][0] == "5m"
    assert {p["code"] for p in body["processes"]} == {"ALM", "CAL", "DIS"}


def test_calidad_creates_an_action_fills_its_plan_and_exports_it(client, world) -> None:
    _manage(world, client)
    created = client.post(
        "/api/v1/action-plans",
        json={
            "origin": "AE",
            "process_id": str(PROCESSES["ALM"].id),
            "action_type": "correctiva",
            "reported_on": "2026-07-06",
            "finding": "Hallazgo",
        },
    )
    assert created.status_code == 201
    plan = created.json()["plan"]
    assert plan["code"] == "AE-060726-201-ALM-1" and plan["status"] == "abierta"

    rows = client.put(
        f"/api/v1/action-plans/{plan['id']}/activities",
        json=[
            {
                "description": "Capacitar",
                "due_on": "2026-11-01",
                "responsible_user_ids": [str(world.owner.user_id)],
            }
        ],
    ).json()
    activity = rows["plan"]["activities_detail"][0]
    assert activity["overdue"] is True and rows["plan"]["status"] == "en_proceso"
    owner = str(world.owner.user_id)
    assert activity["responsible_users"] == [{"id": owner, "display_name": f"Persona {owner[:4]}"}]

    done = client.post(
        f"/api/v1/action-plans/{plan['id']}/rows/{activity['id']}/done", json={"done": True}
    ).json()
    assert done["plan"]["status"] == "cerrada"
    assert (
        client.put(
            f"/api/v1/action-plans/{plan['id']}/causes",
            json=[{"description": "x", "category": "nada"}],
        ).status_code
        == 422
    )

    export = client.get("/api/v1/action-plans/export")
    assert export.status_code == 200
    sheet = openpyxl.load_workbook(io.BytesIO(export.content))["Base datos"]
    assert sheet["E7"].value == "AE-060726-201-ALM-1"
    page = client.get("/api/v1/action-plans", params={"status": "cerrada"}).json()
    assert page["total"] == 1 and page["items"][0]["activities_done"] == 1
