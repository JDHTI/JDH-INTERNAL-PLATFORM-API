"""Modules switched on or off per area (``area_modules``) and what that changes."""

from __future__ import annotations

import pytest

from app.core.errors import ForbiddenError, InvalidInputError, NotFoundError
from app.modules.areas.application.dto import CreateAreaCommand
from app.modules.areas.domain.catalog import AreaModule, clean_module_config, is_enabled
from app.modules.masters.infrastructure.access import AreaMasterAccess
from tests.modules.fakes import principal


def test_a_stored_choice_beats_the_catalog_default() -> None:
    assert is_enabled("bi", "maestros") and not is_enabled("cartera", "maestros")
    assert is_enabled("cartera", "documentos")
    overrides = {
        "cartera": {"maestros": AreaModule(True)},
        "bi": {"documentos": AreaModule(False)},
    }
    assert is_enabled("cartera", "maestros", overrides)
    assert not is_enabled("bi", "documentos", overrides)
    assert not is_enabled("bi", "no-existe", overrides)


def test_only_maestros_takes_settings() -> None:
    assert clean_module_config("maestros", {"masters": [" Clientes", "clientes", "productos"]}) == {
        "masters": ["clientes", "productos"]
    }
    assert clean_module_config("maestros", {"masters": None}) == {}
    assert clean_module_config("documentos", None) == {}
    with pytest.raises(InvalidInputError):
        clean_module_config("documentos", {"masters": []})
    with pytest.raises(InvalidInputError):
        clean_module_config("maestros", {"otra": 1})
    with pytest.raises(InvalidInputError):
        clean_module_config("maestros", {"masters": "clientes"})


async def _area_with_member(areas, admin, login, slug, email, grants):
    await areas.create_area(CreateAreaCommand(slug=slug, name=slug.title()), admin)
    role = await areas.create_role(slug, f"Rol {email}", admin)
    await areas.set_role_permissions(slug, role.id, grants, admin)
    person = await login(email)
    await areas.add_member(slug, person.email, role.id, admin)
    return person


async def test_switching_a_module_on_lets_the_area_grant_it(areas, area_repo, admin, login) -> None:
    await areas.create_area(CreateAreaCommand(slug="cartera", name="Cartera"), admin)
    role = await areas.create_role("cartera", "Analista", admin)

    with pytest.raises(InvalidInputError, match="no está activo"):
        await areas.set_role_permissions("cartera", role.id, [("maestros", "read")], admin)

    state = await areas.set_area_module(
        "cartera", "maestros", True, {"masters": ["clientes", "proveedores"]}, admin
    )
    await areas.set_role_permissions("cartera", role.id, [("maestros", "read")], admin)
    ana = await login("ana@jdh.com.co")
    await areas.add_member("cartera", ana.email, role.id, admin)

    assert state.is_enabled and state.config == {"masters": ["clientes", "proveedores"]}
    assert (await areas.get_area("cartera", ana)).my_modules["maestros"] == "read"
    assert await areas.module_configs(ana, "maestros") == {
        "cartera": {"masters": ["clientes", "proveedores"]}
    }
    event = area_repo.events[-1]
    assert event.action == "member_added"
    toggled = next(e for e in area_repo.events if e.action == "module_toggled")
    assert toggled.detail["before"] == {"enabled": False, "config": {}}
    assert toggled.detail["after"]["enabled"] is True


async def test_switching_a_module_off_hides_it_and_keeps_the_grants(
    areas, area_repo, admin, login
) -> None:
    ana = await _area_with_member(
        areas, admin, login, "bi", "ana@jdh.com.co", [("validador-pedidos", "manage")]
    )
    assert await areas.allows(ana, "bi", "validador-pedidos", "manage")

    await areas.set_area_module("bi", "validador-pedidos", False, None, admin)

    assert not await areas.allows(ana, "bi", "validador-pedidos")
    assert not await areas.allows(ana, None, "validador-pedidos")
    assert "validador-pedidos" not in (await areas.get_area("bi", ana)).my_modules
    # The grant stays stored; it only stops counting.
    assert any("validador-pedidos" in grants for grants in area_repo.permissions.values())

    await areas.set_area_module("bi", "validador-pedidos", True, None, admin)
    assert await areas.allows(ana, "bi", "validador-pedidos", "manage")


async def test_resetting_a_module_returns_it_to_the_catalog_default(
    areas, area_repo, admin, login
) -> None:
    await areas.create_area(CreateAreaCommand(slug="cartera", name="Cartera"), admin)
    await areas.set_area_module("cartera", "maestros", True, {"masters": ["clientes"]}, admin)
    await areas.set_area_module("cartera", "documentos", False, None, admin)

    state = await areas.reset_area_module("cartera", "maestros", admin)
    assert not state.is_enabled and state.is_default and state.config == {}
    assert (await areas.reset_area_module("cartera", "documentos", admin)).is_enabled

    (cartera,) = await areas.area_modules(admin)
    assert cartera.modules["maestros"].is_default and not cartera.modules["maestros"].is_enabled
    assert cartera.modules["documentos"].is_default and cartera.modules["documentos"].is_enabled
    reset = [e for e in area_repo.events if e.detail and e.detail.get("reset")]
    assert [e.detail["module"] for e in reset] == ["maestros", "documentos"]
    assert reset[0].detail["before"] == {"enabled": True, "config": {"masters": ["clientes"]}}

    # Nothing stored: no event, same answer.
    count = len(area_repo.events)
    assert (await areas.reset_area_module("cartera", "maestros", admin)).is_default
    assert len(area_repo.events) == count
    ana = await login("ana@jdh.com.co")
    with pytest.raises(ForbiddenError):
        await areas.reset_area_module("cartera", "maestros", ana)
    with pytest.raises(NotFoundError):
        await areas.reset_area_module("cartera", "no-existe", admin)


async def test_only_admins_switch_modules_and_moderators_see_them(areas, admin, login) -> None:
    await areas.create_area(CreateAreaCommand(slug="bi", name="BI"), admin)
    ana = await login("ana@jdh.com.co")
    moderator = principal("auditor@jdh.com.co", role="moderator")

    with pytest.raises(ForbiddenError):
        await areas.set_area_module("bi", "maestros", False, None, ana)
    with pytest.raises(ForbiddenError):
        await areas.set_area_module("bi", "maestros", False, None, moderator)
    with pytest.raises(ForbiddenError):
        await areas.area_modules(ana)
    with pytest.raises(NotFoundError):
        await areas.set_area_module("bi", "no-existe", True, None, admin)

    (bi,) = await areas.area_modules(moderator)
    assert bi.modules["maestros"].is_enabled and bi.modules["maestros"].is_default
    assert not bi.modules["pqrsf"].is_enabled


async def test_masters_follow_the_areas_where_the_module_is_on(areas, admin, login) -> None:
    access = AreaMasterAccess(areas)
    ana = await _area_with_member(
        areas, admin, login, "bi", "ana@jdh.com.co", [("maestros", "read")]
    )
    await areas.create_area(CreateAreaCommand(slug="cartera", name="Cartera"), admin)
    await areas.set_area_module("cartera", "maestros", True, {"masters": ["clientes"]}, admin)
    role = await areas.create_role("cartera", "Analista", admin)
    await areas.set_role_permissions("cartera", role.id, [("maestros", "manage")], admin)
    luis = await login("luis@jdh.com.co")
    await areas.add_member("cartera", luis.email, role.id, admin)

    # BI has no list: every master. Cartera only clientes.
    assert await access.visible_masters(ana) is None
    assert await access.visible_masters(luis) == frozenset({"clientes"})
    assert await access.area_masters("cartera") == frozenset({"clientes"})
    assert await access.area_masters("bi") is None
    await areas.set_area_module("cartera", "maestros", False, None, admin)
    assert await access.area_masters("cartera") == frozenset()
    await areas.set_area_module("cartera", "maestros", True, {"masters": ["clientes"]}, admin)
    # Personal data: whoever manages the module, and administrators.
    assert not await access.can_see_private(ana)
    assert await access.can_see_private(luis) and await access.can_see_private(admin)
    outsider = await login("nadie@jdh.com.co")
    assert await access.visible_masters(outsider) == frozenset()
