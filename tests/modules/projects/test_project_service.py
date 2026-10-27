import uuid
from datetime import date

import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.areas.application.dto import CreateAreaCommand
from app.modules.projects.application.dto import (
    CreateProjectCommand,
    FolderCommand,
    ProjectView,
    UpdateProjectCommand,
    project_payload,
)
from app.modules.projects.domain.entities import POSITION_STEP
from app.modules.projects.domain.ports import ProjectFilters
from app.modules.projects.presentation.router import ProjectResponse

START = date(2026, 10, 1)


async def folder(projects, user, name="Infraestructura", **kw):
    return (await projects.create_folder("ti", FolderCommand(name=name, **kw), user)).folder


def new(folder_id, name="Migración a la nube", **kw) -> CreateProjectCommand:
    return CreateProjectCommand(folder_id=folder_id, name=name, start_date=START, **kw)


def patch(**kw) -> UpdateProjectCommand:
    return UpdateProjectCommand(fields=frozenset(kw), **kw)


async def test_module_is_only_on_in_enabled_areas(projects, areas, admin, ti) -> None:
    await areas.create_area(CreateAreaCommand(slug="bi", name="BI"), admin)

    with pytest.raises(NotFoundError, match="no está habilitado"):
        await projects.list_folders("bi", admin)
    assert await projects.list_folders("TI", admin) == []


async def test_outsiders_cannot_enter(projects, login, ti) -> None:
    outsider = await login("pedro@jdh.com.co")

    with pytest.raises(ForbiddenError):
        await projects.list_folders("ti", outsider)


async def test_create_project_applies_defaults_and_publishes(projects, channel, ti) -> None:
    area, ana, *_ = ti
    infra = await folder(projects, ana, color="blue")

    view = await projects.create_project("ti", new(infra.id, name="  Migración   a la nube "), ana)

    p = view.project
    assert (p.name, p.status, p.priority, p.color) == (
        "Migración a la nube",
        "created",
        "medium",
        "default",
    )
    assert (p.position, p.version, p.is_archived) == (POSITION_STEP, 1, False)
    assert p.created_by == p.updated_by == ana.email
    event = channel.events("project.")[-1]
    assert event["type"] == "project.created"
    assert event["area_id"] == str(area.id)
    assert event["data"] == project_payload(view)
    assert event["actor"]["email"] == ana.email


async def test_event_payload_matches_http_response(projects, ti) -> None:
    _, ana, *_ = ti
    infra = await folder(projects, ana)
    view = await projects.create_project("ti", new(infra.id, end_date=date(2026, 12, 1)), ana)

    response = ProjectResponse.model_validate(project_payload(view))

    assert response.model_dump(mode="json") == project_payload(view)


async def test_new_projects_go_last_in_their_folder(projects, ti) -> None:
    _, ana, *_ = ti
    infra = await folder(projects, ana)
    first = await projects.create_project("ti", new(infra.id, "A"), ana)
    second = await projects.create_project("ti", new(infra.id, "B"), ana)

    assert second.project.position == first.project.position + POSITION_STEP


@pytest.mark.parametrize(
    "command",
    [
        {"end_date": date(2026, 9, 30)},
        {"status": "done"},
        {"priority": "urgent"},
        {"color": "#ff0000"},
    ],
)
async def test_create_validations(projects, ti, command) -> None:
    _, ana, *_ = ti
    infra = await folder(projects, ana)

    with pytest.raises(InvalidInputError):
        await projects.create_project("ti", new(infra.id, **command), ana)


async def test_owner_must_be_an_active_user(projects, directory, ti) -> None:
    _, ana, luis, _ = ti
    infra = await folder(projects, ana)
    view = await projects.create_project("ti", new(infra.id, owner_user_id=luis.user_id), ana)
    assert view.owner.email == luis.email

    gone = directory.sign_in("ex@jdh.com.co", is_active=False)
    with pytest.raises(InvalidInputError):
        await projects.create_project("ti", new(infra.id, owner_user_id=gone.user_id), ana)


async def test_update_changes_only_sent_fields_and_bumps_version(projects, channel, ti) -> None:
    _, ana, luis, _ = ti
    infra = await folder(projects, ana)
    created = await projects.create_project("ti", new(infra.id, description="Fase 1"), ana)

    view = await projects.update_project(
        "ti", created.project.id, patch(status="in_progress", end_date=date(2026, 11, 30)), luis
    )

    p = view.project
    assert (p.status, p.end_date, p.description) == ("in_progress", date(2026, 11, 30), "Fase 1")
    assert (p.version, p.updated_by, p.created_by) == (2, luis.email, ana.email)
    event = channel.events("project.updated")[-1]
    assert event["changed"] == ["end_date", "status"]
    assert event["data"]["version"] == 2


async def test_update_can_clear_optional_fields(projects, ti) -> None:
    _, ana, luis, _ = ti
    infra = await folder(projects, ana)
    created = await projects.create_project(
        "ti",
        new(infra.id, description="x", end_date=date(2026, 12, 1), owner_user_id=luis.user_id),
        ana,
    )

    view = await projects.update_project(
        "ti", created.project.id, patch(description=None, end_date=None, owner_user_id=None), ana
    )

    assert (view.project.description, view.project.end_date, view.owner) == (None, None, None)


async def test_update_rejects_clearing_required_fields(projects, ti) -> None:
    _, ana, *_ = ti
    infra = await folder(projects, ana)
    created = await projects.create_project("ti", new(infra.id), ana)

    for field in ("name", "status", "start_date", "folder_id"):
        with pytest.raises(InvalidInputError):
            await projects.update_project("ti", created.project.id, patch(**{field: None}), ana)


async def test_update_checks_dates_against_the_stored_ones(projects, ti) -> None:
    _, ana, *_ = ti
    infra = await folder(projects, ana)
    created = await projects.create_project("ti", new(infra.id, end_date=date(2026, 10, 15)), ana)

    with pytest.raises(InvalidInputError):
        await projects.update_project(
            "ti", created.project.id, patch(start_date=date(2026, 10, 20)), ana
        )


async def test_move_to_another_folder_goes_last_unless_position_is_given(projects, ti) -> None:
    _, ana, *_ = ti
    infra = await folder(projects, ana, "Infraestructura")
    apps = await folder(projects, ana, "Aplicaciones")
    await projects.create_project("ti", new(apps.id, "Existente"), ana)
    moving = await projects.create_project("ti", new(infra.id, "Mover"), ana)

    moved = await projects.update_project("ti", moving.project.id, patch(folder_id=apps.id), ana)
    assert (moved.project.folder_id, moved.project.position) == (apps.id, 2 * POSITION_STEP)

    placed = await projects.update_project(
        "ti", moving.project.id, patch(folder_id=infra.id, position=512.0), ana
    )
    assert (placed.project.folder_id, placed.project.position) == (infra.id, 512.0)


async def test_move_rejects_folders_of_other_areas(projects, ti) -> None:
    _, ana, *_ = ti
    infra = await folder(projects, ana)
    created = await projects.create_project("ti", new(infra.id), ana)

    with pytest.raises(NotFoundError):
        await projects.update_project("ti", created.project.id, patch(folder_id=uuid.uuid4()), ana)


async def test_position_must_be_finite(projects, ti) -> None:
    _, ana, *_ = ti
    infra = await folder(projects, ana)
    created = await projects.create_project("ti", new(infra.id), ana)

    with pytest.raises(InvalidInputError):
        await projects.update_project("ti", created.project.id, patch(position=float("nan")), ana)


async def test_archive_hides_from_default_listing_and_restore_brings_back(
    projects, channel, ti
) -> None:
    _, ana, luis, _ = ti
    infra = await folder(projects, ana)
    created = await projects.create_project("ti", new(infra.id), ana)

    archived = await projects.set_archived("ti", created.project.id, True, luis)
    assert archived.project.is_archived and archived.project.archived_at is not None
    assert await projects.list_projects("ti", ProjectFilters(), ana) == []
    listed = await projects.list_projects("ti", ProjectFilters(include_archived=True), ana)
    assert [v.project.id for v in listed] == [created.project.id]
    assert (await projects.list_folders("ti", ana))[0].project_count == 0
    assert channel.events("project.updated")[-1]["changed"] == ["archived_at", "is_archived"]

    restored = await projects.set_archived("ti", created.project.id, False, luis)
    assert not restored.project.is_archived and restored.project.archived_at is None


async def test_archiving_twice_is_a_no_op(projects, channel, ti) -> None:
    _, ana, *_ = ti
    infra = await folder(projects, ana)
    created = await projects.create_project("ti", new(infra.id), ana)
    await projects.set_archived("ti", created.project.id, True, ana)
    count = len(channel.published)

    again = await projects.set_archived("ti", created.project.id, True, ana)

    assert again.project.version == 2
    assert len(channel.published) == count


async def test_list_filters_by_status(projects, ti) -> None:
    _, ana, *_ = ti
    infra = await folder(projects, ana)
    await projects.create_project("ti", new(infra.id, "A", status="ideation"), ana)
    await projects.create_project("ti", new(infra.id, "B", status="in_progress"), ana)

    listed = await projects.list_projects("ti", ProjectFilters(statuses=("IN_PROGRESS",)), ana)

    assert [v.project.name for v in listed] == ["B"]


@pytest.mark.parametrize("who", ["creator", "responsible", "admin"])
async def test_permanent_delete_by_managers(projects, project_repo, admin, ti, who) -> None:
    _, ana, _, marta = ti
    infra = await folder(projects, ana)
    created = await projects.create_project("ti", new(infra.id), ana)
    user = {"creator": ana, "responsible": marta, "admin": admin}[who]

    await projects.delete_project("ti", created.project.id, user)

    assert created.project.id not in project_repo.projects


async def test_other_members_cannot_delete_permanently(projects, ti) -> None:
    _, ana, luis, _ = ti
    infra = await folder(projects, ana)
    created = await projects.create_project("ti", new(infra.id), ana)

    with pytest.raises(ForbiddenError):
        await projects.delete_project("ti", created.project.id, luis)
    with pytest.raises(ForbiddenError):
        await projects.delete_folder("ti", infra.id, luis)


async def test_delete_publishes_the_id_and_folder(projects, channel, ti) -> None:
    _, ana, *_ = ti
    infra = await folder(projects, ana)
    created = await projects.create_project("ti", new(infra.id), ana)

    await projects.delete_project("ti", created.project.id, ana)

    assert channel.events("project.deleted")[-1]["data"] == {
        "id": str(created.project.id),
        "folder_id": str(infra.id),
    }


async def test_folder_names_are_unique_per_area(projects, ti) -> None:
    _, ana, luis, _ = ti
    await folder(projects, ana, "Infraestructura")

    with pytest.raises(ConflictError):
        await folder(projects, luis, "INFRAESTRUCTURA")


async def test_update_folder_color_and_publish(projects, channel, ti) -> None:
    _, ana, luis, _ = ti
    infra = await folder(projects, ana)

    view = await projects.update_folder(
        "ti", infra.id, FolderCommand(color="green", fields=frozenset({"color"})), luis
    )

    assert (view.folder.color, view.folder.name) == ("green", "Infraestructura")
    assert channel.events("folder.updated")[-1]["changed"] == ["color"]


async def test_folder_with_projects_even_archived_cannot_be_deleted(projects, ti) -> None:
    _, ana, *_ = ti
    infra = await folder(projects, ana)
    created = await projects.create_project("ti", new(infra.id), ana)
    await projects.set_archived("ti", created.project.id, True, ana)

    with pytest.raises(ConflictError):
        await projects.delete_folder("ti", infra.id, ana)

    await projects.delete_project("ti", created.project.id, ana)
    await projects.delete_folder("ti", infra.id, ana)
    assert await projects.list_folders("ti", ana) == []


async def test_publish_failure_does_not_undo_the_change(projects, channel, project_repo, ti):
    _, ana, *_ = ti
    infra = await folder(projects, ana)
    channel.fail_publish = True

    view = await projects.create_project("ti", new(infra.id), ana)

    assert isinstance(view, ProjectView)
    assert view.project.id in project_repo.projects


async def test_can_delete_any_is_for_responsible_and_admins(projects, admin, ti) -> None:
    _, ana, _, marta = ti

    assert await projects.can_delete_any("ti", ana) is False
    assert await projects.can_delete_any("ti", marta) is True
    assert await projects.can_delete_any("ti", admin) is True


async def test_people_search_is_open_to_members_and_skips_inactive(
    projects, directory, login, ti
) -> None:
    _, ana, *_ = ti
    directory.sign_in("luisa.ex@jdh.com.co", is_active=False)

    found = await projects.search_people("ti", " luis ", ana, limit=5)

    assert [u.email for u in found] == ["luis@jdh.com.co"]
    outsider = await login("pedro@jdh.com.co")
    with pytest.raises(ForbiddenError):
        await projects.search_people("ti", "", outsider)
