import pytest
from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.areas.application.dto import CreateAreaCommand, UpdateAreaCommand


async def _area_with_member(areas, admin, member, slug="bi", role_name="Analista BI"):
    await areas.create_area(CreateAreaCommand(slug=slug, name=slug.upper()), admin)
    role = await areas.create_role(slug, role_name, admin)
    await areas.add_member(slug, member.email, role.id, admin)
    return role


async def test_only_platform_admins_manage_areas(areas, admin, login) -> None:
    ana = await login("ana@jdh.com.co")
    with pytest.raises(ForbiddenError):
        await areas.create_area(CreateAreaCommand(slug="ti", name="TI"), ana)

    view = await areas.create_area(
        CreateAreaCommand(slug="ti", name="  TI  ", icon="Server", sort_order=10), admin
    )
    assert (view.slug, view.name, view.icon, view.sort_order) == ("ti", "TI", "Server", 10)


async def test_slug_and_name_are_unique_and_validated(areas, admin) -> None:
    await areas.create_area(CreateAreaCommand(slug="cartera", name="Cartera"), admin)
    with pytest.raises(ConflictError, match="identificador"):
        await areas.create_area(CreateAreaCommand(slug="cartera", name="Otra"), admin)
    with pytest.raises(ConflictError, match="nombre"):
        await areas.create_area(CreateAreaCommand(slug="otra", name="CARTERA"), admin)
    with pytest.raises(InvalidInputError):
        await areas.create_area(CreateAreaCommand(slug="Tesorería", name="Tesorería"), admin)


async def test_non_members_only_see_their_areas(areas, admin, login) -> None:
    ana = await login("ana@jdh.com.co")
    await _area_with_member(areas, admin, ana, slug="bi", role_name="Líder BI")
    await areas.create_area(
        CreateAreaCommand(slug="cartera", name="Cartera", responsible_email=ana.email), admin
    )
    await areas.create_area(CreateAreaCommand(slug="mercadeo", name="Mercadeo"), admin)

    mine = await areas.list_areas(ana)
    assert [(a.slug, a.is_responsible, a.my_role.name if a.my_role else None) for a in mine] == [
        ("bi", False, "Líder BI"),
        ("cartera", True, None),
    ]
    assert {a.slug for a in await areas.list_areas(admin)} == {"bi", "cartera", "mercadeo"}

    with pytest.raises(ForbiddenError):
        await areas.get_area("mercadeo", ana)


async def test_responsible_must_have_signed_in(areas, admin, login) -> None:
    command = CreateAreaCommand(slug="ti", name="TI", responsible_email="Nuevo@JDH.com.co")
    with pytest.raises(NotFoundError):
        await areas.create_area(command, admin)

    await login("nuevo@jdh.com.co")
    view = await areas.create_area(command, admin)
    assert view.responsible.email == "nuevo@jdh.com.co"

    updated = await areas.update_area(
        "ti", UpdateAreaCommand(fields=frozenset({"responsible_email"})), admin
    )
    assert updated.responsible is None


async def test_member_role_must_belong_to_the_same_area(areas, admin, login) -> None:
    ana = await login("ana@jdh.com.co")
    await areas.create_area(CreateAreaCommand(slug="bi", name="BI"), admin)
    await areas.create_area(CreateAreaCommand(slug="mercadeo", name="Mercadeo"), admin)
    mkt_role = await areas.create_role("mercadeo", "Coordinador", admin)
    with pytest.raises(NotFoundError):
        await areas.add_member("bi", ana.email, mkt_role.id, admin)


async def test_role_rules(areas, admin, login) -> None:
    ana = await login("ana@jdh.com.co")
    role = await _area_with_member(areas, admin, ana)
    with pytest.raises(ConflictError):
        await areas.create_role("bi", "analista bi", admin)
    with pytest.raises(ConflictError):
        await areas.delete_role("bi", role.id, admin)
    with pytest.raises(ConflictError):
        await areas.add_member("bi", ana.email, role.id, admin)


async def test_members_can_read_their_team(areas, admin, login) -> None:
    ana = await login("ana@jdh.com.co")
    await _area_with_member(areas, admin, ana)
    members = await areas.list_members("bi", ana)
    assert [(m.user.email, m.role.name) for m in members] == [("ana@jdh.com.co", "Analista BI")]
    with pytest.raises(ForbiddenError):
        await areas.create_role("bi", "Líder BI", ana)


async def test_area_with_folders_cannot_be_deleted(areas, admin, area_repo) -> None:
    view = await areas.create_area(CreateAreaCommand(slug="bi", name="BI"), admin)
    area_repo.areas_with_folders.add(view.id)
    with pytest.raises(ConflictError):
        await areas.delete_area("bi", admin)
