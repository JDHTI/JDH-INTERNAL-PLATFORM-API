"""HTTP: the module read gates of the workspace and the permissions panel."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.core.error_handlers import register_error_handlers
from app.main import create_app
from app.modules.areas.application.dto import CreateAreaCommand
from app.modules.areas.application.service import AreaService
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.identity.presentation.principal import get_current_principal
from tests.modules.fakes import FakeAreaRepository, FakeDirectory, principal

# The gates of this module are the real ones (see tests/conftest.py).
REAL_PERMISSION_GATES = True
BASE = "/api/v1"


class World:
    def __init__(self) -> None:
        self.directory = FakeDirectory()
        self.repository = FakeAreaRepository()
        self.areas = AreaService(self.repository, self.directory)
        self.admin = self.directory.sign_in("analista.ti@jdh.com.co", admin=True)
        self.caller = self.admin

    def run(self, coroutine):
        return asyncio.run(coroutine)

    def member(self, slug: str, email: str, grants: list[tuple[str, str]]):
        person = self.directory.sign_in(email)
        role = self.run(self.areas.create_role(slug, f"Rol {email}", self.admin))
        self.run(self.areas.set_role_permissions(slug, role.id, grants, self.admin))
        self.run(self.areas.add_member(slug, email, role.id, self.admin))
        return person


@pytest.fixture
def world() -> World:
    world = World()
    for slug, name in (("bi", "BI"), ("comercial", "Comercial"), ("calidad", "Calidad")):
        world.run(world.areas.create_area(CreateAreaCommand(slug=slug, name=name), world.admin))
    return world


@pytest.fixture
def client(world):
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[get_current_principal] = lambda: world.caller
    with (
        app.state.container.v1_container.area_service.override(world.areas),
        TestClient(app) as test_client,
    ):
        yield test_client
    app.dependency_overrides.clear()


def test_a_plain_user_is_kept_out_of_the_workspace_reads(client, world) -> None:
    world.caller = world.directory.sign_in("nadie@jdh.com.co")

    for path in (
        "/order-validations",
        "/commercial/products",
        "/pqrsf/cases/suggestions?q=abc",
        "/pqrsf/supplier-contacts",
        "/dwh-monitoring/overview",
        "/monitoring/board",
        "/quotations",
    ):
        response = client.get(f"{BASE}{path}")
        assert response.status_code == 403, (path, response.text)
    # The flags the UI reads keep answering.
    assert client.get(f"{BASE}/order-validations/permissions").json() == {"can_manage": False}


def test_bi_and_comercial_roles_manage_the_order_validator(client, world) -> None:
    permissions = f"{BASE}/order-validations/permissions"

    world.caller = world.member("bi", "analista@jdh.com.co", [("validador-pedidos", "manage")])
    assert client.get(f"{BASE}/order-validations/catalogs").status_code == 200
    assert client.get(permissions).json() == {"can_manage": True}

    world.caller = world.member(
        "comercial", "vendedor@jdh.com.co", [("validador-pedidos", "manage")]
    )
    assert client.get(permissions).json() == {"can_manage": True}

    world.caller = world.member("bi", "lector@jdh.com.co", [("validador-pedidos", "read")])
    assert client.get(f"{BASE}/order-validations/catalogs").status_code == 200
    assert client.get(permissions).json() == {"can_manage": False}

    world.caller = world.member("bi", "sin-permisos@jdh.com.co", [])
    assert client.get(f"{BASE}/order-validations/catalogs").status_code == 403


def test_modules_are_switched_per_area(client, world) -> None:
    body = client.get(f"{BASE}/permissions/modules").json()
    keys = [module["key"] for module in body["modules"]]
    bi = next(area for area in body["areas"] if area["slug"] == "bi")
    assert "maestros" in keys and bi["modules"]["maestros"]["enabled"] is True
    assert bi["modules"]["pqrsf"] == {"enabled": False, "config": {}, "is_default": True}

    response = client.put(
        f"{BASE}/areas/comercial/modules/maestros",
        json={"enabled": True, "config": {"masters": ["clientes"]}},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "module": "maestros",
        "enabled": True,
        "config": {"masters": ["clientes"]},
        "is_default": False,
    }
    catalog = client.get(f"{BASE}/permissions/catalog").json()
    comercial = next(area for area in catalog["areas"] if area["slug"] == "comercial")
    assert "maestros" in [m["key"] for m in comercial["modules"]]

    assert client.put(f"{BASE}/areas/bi/modules/nada", json={"enabled": True}).status_code == 404
    bad = client.put(
        f"{BASE}/areas/bi/modules/documentos", json={"enabled": True, "config": {"x": 1}}
    )
    assert bad.status_code == 422

    world.caller = world.member("bi", "lector@jdh.com.co", [("maestros", "read")])
    assert client.get(f"{BASE}/permissions/modules").status_code == 403
    denied = client.put(f"{BASE}/areas/bi/modules/maestros", json={"enabled": False})
    assert denied.status_code == 403


def test_the_gate_follows_the_grants(world) -> None:
    app = FastAPI()
    app.state.container = SimpleNamespace(
        v1_container=SimpleNamespace(area_service=lambda: world.areas)
    )
    register_error_handlers(app)
    app.dependency_overrides[get_current_principal] = lambda: world.caller

    @app.get("/read", dependencies=[Depends(require_permission("validador-pedidos"))])
    async def read() -> dict:
        return {"ok": True}

    @app.get(
        "/manage",
        dependencies=[Depends(require_permission("validador-pedidos", action="manage"))],
    )
    async def manage() -> dict:
        return {"ok": True}

    @app.get("/any", dependencies=[Depends(require_permission("maestros", "cotizador"))])
    async def any_of() -> dict:
        return {"ok": True}

    client = TestClient(app)
    world.caller = world.member("bi", "lector@jdh.com.co", [("validador-pedidos", "read")])
    assert client.get("/read").status_code == 200
    assert client.get("/manage").status_code == 403
    assert client.get("/any").status_code == 403

    world.caller = world.member("comercial", "vendedor@jdh.com.co", [("cotizador", "manage")])
    assert client.get("/any").status_code == 200
    assert client.get("/read").status_code == 403

    world.caller = principal("auditor@jdh.com.co", role="moderator")
    assert client.get("/read").status_code == 200 and client.get("/manage").status_code == 403


def test_my_permissions_and_the_catalog(client, world) -> None:
    world.caller = world.member("calidad", "ana@jdh.com.co", [("pqrsf", "manage")])

    mine = client.get(f"{BASE}/permissions/me").json()
    catalog = client.get(f"{BASE}/permissions/catalog").json()

    assert mine["role"] == "user" and mine["is_platform_admin"] is False
    assert [(a["slug"], a["modules"]) for a in mine["areas"]] == [("calidad", {"pqrsf": "manage"})]
    calidad = next(a for a in catalog["areas"] if a["slug"] == "calidad")
    assert [m["key"] for m in calidad["modules"]] == ["documentos", "pqrsf", "planes-accion"]
    assert catalog["actions"] == ["read", "manage"] and catalog["wildcard"] == "*"


def test_admins_edit_grants_and_moderators_read_the_log(client, world) -> None:
    leader = asyncio.run(world.areas.list_roles("bi", world.admin))[0]
    roles = client.get(f"{BASE}/areas/bi/roles").json()
    assert roles[0]["id"] == str(leader.id) and roles[0]["is_leader"] is True
    fixed = client.put(
        f"{BASE}/areas/bi/roles/{leader.id}/permissions",
        json={"grants": [{"module": "cotizador", "action": "read"}]},
    )
    assert fixed.status_code == 409
    assert client.delete(f"{BASE}/areas/bi/roles/{leader.id}").status_code == 409
    role = asyncio.run(world.areas.create_role("bi", "Analista BI", world.admin))

    updated = client.put(
        f"{BASE}/areas/bi/roles/{role.id}/permissions",
        json={"grants": [{"module": "cotizador", "action": "manage"}]},
    )
    assert updated.status_code == 200
    assert updated.json()["grants"] == [{"module": "cotizador", "action": "manage"}]
    bad = client.put(
        f"{BASE}/areas/bi/roles/{role.id}/permissions",
        json={"grants": [{"module": "pqrsf", "action": "read"}]},
    )
    assert bad.status_code == 422

    world.caller = principal("auditor@jdh.com.co", role="moderator")
    log = client.get(f"{BASE}/permissions/audit", params={"area_slug": "bi"}).json()
    assert log["items"][0]["action"] == "role_permissions_set"
    assert client.get(f"{BASE}/areas/bi/roles/{role.id}/permissions").status_code == 200
    denied = client.put(
        f"{BASE}/areas/bi/roles/{role.id}/permissions",
        json={"grants": []},
    )
    assert denied.status_code == 403

    world.caller = world.directory.sign_in("nadie@jdh.com.co")
    assert client.get(f"{BASE}/permissions/audit").status_code == 403
