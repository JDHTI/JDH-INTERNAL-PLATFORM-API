"""Workspace permissions: global roles, area role grants, policies and the log."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError
from app.modules.areas.application.dto import CreateAreaCommand
from app.modules.areas.domain.catalog import modules_for, validate_grant
from app.modules.areas.domain.entities import AccessProfile, AreaGrants
from app.modules.areas.infrastructure.policies import PermissionPolicy
from app.modules.documents.application.dto import UploadCommand
from tests.modules.fakes import principal


def _profile(role="user", **areas) -> AccessProfile:
    return AccessProfile(
        role=role,
        areas={
            slug: AreaGrants(area_id=uuid4(), slug=slug, grants=grants)
            for slug, grants in areas.items()
        },
    )


def test_global_roles_bypass_or_read_everything() -> None:
    for role in ("owner", "admin"):
        assert _profile(role).allows("calidad", "pqrsf", "manage")
    moderator = _profile("moderator")
    assert moderator.allows("bi", "cotizador", "read")
    assert not moderator.allows("bi", "cotizador", "manage")
    assert _profile().allows("calidad", "pqrsf", "read") is False


def test_area_grants_wildcard_module_and_implication() -> None:
    full = _profile(calidad={"*": "manage"})
    assert full.allows("calidad", "pqrsf", "manage") and full.allows("calidad", "documentos")
    assert not full.allows("bi", "cotizador", "read")

    reader = _profile(calidad={"*": "read"})
    assert reader.allows("calidad", "pqrsf", "read")
    assert not reader.allows("calidad", "pqrsf", "manage")

    partial = _profile(bi={"cotizador": "manage", "apoyos": "read"})
    assert partial.allows("bi", "cotizador", "read")
    assert partial.allows("bi", "apoyos", "read") and not partial.allows("bi", "apoyos", "manage")
    assert not partial.allows("bi", "maestros", "read")
    # No area given: any area where the module exists.
    assert partial.allows(None, "cotizador", "manage")
    assert not partial.allows(None, "pqrsf", "read")


def test_the_responsible_manages_the_whole_area() -> None:
    profile = AccessProfile(
        role="user", areas={"ti": AreaGrants(area_id=uuid4(), slug="ti", is_responsible=True)}
    )
    assert profile.allows("ti", "datawarehouse", "manage")
    assert profile.modules("ti") == {m.key: "manage" for m in modules_for("ti")}


def test_modules_expand_the_wildcard_for_the_menu() -> None:
    profile = _profile(bi={"*": "read", "cotizador": "manage"})

    modules = profile.modules("bi")

    assert modules["cotizador"] == "manage" and modules["apoyos"] == "read"
    assert "pqrsf" not in modules
    assert _profile().modules("bi") == {}


def test_grants_are_checked_against_the_catalog() -> None:
    assert validate_grant("calidad", " PQRSF ", "Read") == ("pqrsf", "read")
    assert validate_grant("tesoreria", "*", "manage") == ("*", "manage")
    assert validate_grant("tesoreria", "documentos", "read") == ("documentos", "read")
    with pytest.raises(InvalidInputError):
        validate_grant("calidad", "cotizador", "read")
    with pytest.raises(InvalidInputError):
        validate_grant("bi", "cotizador", "delete")


async def test_new_areas_get_their_leader_role(areas, area_repo, admin) -> None:
    await areas.create_area(CreateAreaCommand(slug="cartera", name="Cartera"), admin)

    roles = await areas.list_roles("cartera", admin)

    assert [(role.name, role.is_leader) for role in roles] == [("Líder de área", True)]
    assert area_repo.permissions[roles[0].id] == {"*": "manage"}
    assert [event.action for event in area_repo.events] == ["area_created"]


async def test_the_leader_role_is_fixed(areas, area_repo, admin) -> None:
    await areas.create_area(CreateAreaCommand(slug="bi", name="BI"), admin)
    leader = (await areas.list_roles("bi", admin))[0]

    with pytest.raises(ConflictError):
        await areas.set_role_permissions("bi", leader.id, [("cotizador", "read")], admin)
    with pytest.raises(ConflictError):
        await areas.delete_role("bi", leader.id, admin)
    assert area_repo.permissions[leader.id] == {"*": "manage"}

    renamed = await areas.rename_role("bi", leader.id, "Director BI", admin)
    assert renamed.name == "Director BI" and renamed.is_leader
    assert area_repo.events[-1].action == "role_renamed"
    assert area_repo.events[-1].detail == {"before": "Líder de área", "after": "Director BI"}


async def test_the_leader_manages_everything_whatever_its_rows(
    areas, area_repo, admin, login
) -> None:
    await areas.create_area(CreateAreaCommand(slug="bi", name="BI"), admin)
    leader = (await areas.list_roles("bi", admin))[0]
    area_repo.permissions[leader.id] = {}  # even a stored row lost does not narrow it
    ana = await login("ana@jdh.com.co")
    await areas.add_member("bi", ana.email, leader.id, admin)

    view = await areas.get_area("bi", ana)

    assert view.my_role.is_leader
    assert view.my_modules == {m.key: "manage" for m in modules_for("bi")}
    assert await areas.allows(ana, "bi", "maestros", "manage")


async def test_other_roles_take_any_grant_including_the_whole_area(areas, admin) -> None:
    await areas.create_area(CreateAreaCommand(slug="bi", name="BI"), admin)
    analyst = await areas.create_role("bi", "Analista BI", admin)

    _, grants = await areas.set_role_permissions("bi", analyst.id, [("*", "manage")], admin)

    assert [(g.module, g.action) for g in grants] == [("*", "manage")]
    assert not analyst.is_leader
    await areas.delete_role("bi", analyst.id, admin)


async def test_a_new_role_grants_nothing_until_set(areas, area_repo, admin, login) -> None:
    area_repo.default_grants = None
    await areas.create_area(CreateAreaCommand(slug="ti", name="TI"), admin)
    empty = await areas.create_role("ti", "Practicante", admin)
    pedro = await login("pedro@jdh.com.co")
    await areas.add_member("ti", pedro.email, empty.id, admin)

    assert (await areas.get_area("ti", pedro)).my_modules == {}
    assert await areas.allows(pedro, "ti", "documentos") is False
    # The seeded role keeps the whole area.
    seeded = next(r for r in await areas.list_roles("ti", admin) if r.is_leader)
    assert area_repo.permissions[seeded.id] == {"*": "manage"}


async def test_role_grants_decide_what_members_do(areas, area_repo, admin, login) -> None:
    await areas.create_area(CreateAreaCommand(slug="calidad", name="Calidad"), admin)
    reader_role = await areas.create_role("calidad", "Consulta", admin)
    await areas.set_role_permissions(
        "calidad", reader_role.id, [("pqrsf", "read"), ("pqrsf", "read")], admin
    )
    ana = await login("ana@jdh.com.co")
    await areas.add_member("calidad", ana.email, reader_role.id, admin)
    policy = PermissionPolicy(areas, "calidad", "pqrsf")

    assert await policy.can_read(ana) is True
    assert await policy.is_manager(ana) is False
    assert await areas.allows(ana, "calidad", "documentos") is False
    with pytest.raises(ForbiddenError):
        await policy.require(ana)

    role, grants = await areas.set_role_permissions(
        "calidad", reader_role.id, [("pqrsf", "read"), ("pqrsf", "manage")], admin
    )
    assert [(g.module, g.action) for g in grants] == [("pqrsf", "manage")]
    assert await policy.is_manager(ana) is True

    actions = [event.action for event in area_repo.events]
    assert actions == [
        "area_created",
        "role_created",
        "role_permissions_set",
        "member_added",
        "role_permissions_set",
    ]
    last = area_repo.events[-1]
    assert last.detail["before"] == {"pqrsf": "read"}
    assert last.detail["after"] == {"pqrsf": "manage"}


async def test_a_stored_grant_on_a_retired_module_is_ignored(
    areas, area_repo, admin, login
) -> None:
    await areas.create_area(CreateAreaCommand(slug="bi", name="BI"), admin)
    role = await areas.create_role("bi", "Compras", admin)
    # A row written while the module existed (``productos-solicitados`` was retired).
    area_repo.permissions[role.id] = {"productos-solicitados": "manage", "cotizador": "read"}
    ana = await login("ana@jdh.com.co")
    await areas.add_member("bi", ana.email, role.id, admin)

    assert (await areas.get_area("bi", ana)).my_modules == {"cotizador": "read"}
    assert await areas.allows(ana, "bi", "productos-solicitados") is False
    _, grants = await areas.get_role_permissions("bi", role.id, admin)
    assert [(g.module, g.action) for g in grants] == [("cotizador", "read")]
    (view,) = await areas.user_permissions(ana.user_id, admin)
    assert [(g.module, g.action) for g in view.grants] == [("cotizador", "read")]

    # Saving what the panel shows drops the stale row.
    await areas.set_role_permissions("bi", role.id, [("cotizador", "read")], admin)
    assert area_repo.permissions[role.id] == {"cotizador": "read"}


async def test_a_policy_without_area_follows_every_area_of_the_module(areas, admin, login) -> None:
    for slug, name in (("bi", "BI"), ("comercial", "Comercial"), ("calidad", "Calidad")):
        await areas.create_area(CreateAreaCommand(slug=slug, name=name), admin)
    anywhere = PermissionPolicy(areas, None, "validador-pedidos")
    only_comercial = PermissionPolicy(areas, "comercial", "validador-pedidos")

    async def member(slug: str, email: str, grants: list[tuple[str, str]]):
        role = await areas.create_role(slug, f"Rol {email}", admin)
        await areas.set_role_permissions(slug, role.id, grants, admin)
        person = await login(email)
        await areas.add_member(slug, person.email, role.id, admin)
        return person

    analyst = await member("bi", "analista@jdh.com.co", [("validador-pedidos", "manage")])
    seller = await member("comercial", "vendedor@jdh.com.co", [("validador-pedidos", "manage")])
    reader = await member("bi", "lector@jdh.com.co", [("validador-pedidos", "read")])
    quality = await member("calidad", "calidad@jdh.com.co", [("*", "manage")])

    assert await anywhere.is_manager(analyst) and await anywhere.is_manager(seller)
    assert await only_comercial.is_manager(analyst) is False
    assert await anywhere.can_read(reader) and not await anywhere.is_manager(reader)
    # The whole of an area without the module grants nothing on it.
    assert await anywhere.can_read(quality) is False
    moderator = principal("auditor@jdh.com.co", role="moderator")
    assert await anywhere.can_read(moderator) and not await anywhere.is_manager(moderator)
    assert await anywhere.is_manager(admin)


async def test_only_admins_edit_grants_and_moderators_read_them(areas, admin, login) -> None:
    await areas.create_area(CreateAreaCommand(slug="bi", name="BI"), admin)
    role = (await areas.list_roles("bi", admin))[0]
    moderator = principal("auditor@jdh.com.co", role="moderator")
    ana = await login("ana@jdh.com.co")

    _, grants = await areas.get_role_permissions("bi", role.id, moderator)
    assert [(g.module, g.action) for g in grants] == [("*", "manage")]
    with pytest.raises(ForbiddenError):
        await areas.set_role_permissions("bi", role.id, [("cotizador", "read")], moderator)
    with pytest.raises(ForbiddenError):
        await areas.get_role_permissions("bi", role.id, ana)
    analyst = await areas.create_role("bi", "Analista BI", admin)
    with pytest.raises(InvalidInputError):
        await areas.set_role_permissions("bi", analyst.id, [("pqrsf", "read")], admin)

    events, total = await areas.list_events(moderator, area_slug="bi")
    assert total == 2 and [e.action for e in events] == ["role_created", "area_created"]
    with pytest.raises(ForbiddenError):
        await areas.list_events(ana)


async def test_moderators_see_every_area_read_only(areas, admin) -> None:
    await areas.create_area(CreateAreaCommand(slug="ti", name="TI"), admin)
    moderator = principal("auditor@jdh.com.co", role="moderator")

    listed = await areas.list_areas(moderator)

    assert [view.slug for view in listed] == ["ti"]
    assert listed[0].my_modules == {m.key: "read" for m in modules_for("ti")}
    assert await areas.accessible_area_ids(moderator, "documentos") is None
    assert await areas.accessible_area_ids(moderator, "documentos", "manage") == []


async def test_documents_split_read_and_manage(areas, documents, admin, login) -> None:
    await areas.create_area(CreateAreaCommand(slug="bi", name="BI"), admin)
    folder = await documents.create_folder("bi", "Contratos", None, admin)
    viewer = await areas.create_role("bi", "Lector de documentos", admin)
    await areas.set_role_permissions("bi", viewer.id, [("documentos", "read")], admin)
    ana = await login("ana@jdh.com.co")
    await areas.add_member("bi", ana.email, viewer.id, admin)

    assert [view.folder.name for view in await documents.list_folders("bi", ana)] == ["Contratos"]
    with pytest.raises(ForbiddenError):
        await documents.create_folder("bi", "Otra", None, ana)
    with pytest.raises(ForbiddenError):
        await documents.upload(
            "bi",
            folder.folder.id,
            UploadCommand(filename="a.pdf", content=b"%PDF"),
            ana,
        )
