import uuid
from datetime import date

import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.projects.application.dto import (
    CreateEpicCommand,
    CreateProjectCommand,
    CreateTaskCommand,
    FolderCommand,
    UpdateEpicCommand,
    UpdateTaskCommand,
    epic_payload,
    task_payload,
)
from app.modules.projects.domain.entities import POSITION_STEP
from app.modules.projects.domain.ports import ProjectFilters
from app.modules.projects.presentation.router import EpicResponse, TaskResponse


def patch(cls=UpdateTaskCommand, **kw):
    return cls(fields=frozenset(kw), **kw)


@pytest.fixture
async def board(projects, ti):
    """Project "ERP" in folder "Aplicaciones" with one epic "Fase 1"."""

    _, ana, *_ = ti
    folder = (await projects.create_folder("ti", FolderCommand(name="Aplicaciones"), ana)).folder
    project = (
        await projects.create_project(
            "ti",
            CreateProjectCommand(folder_id=folder.id, name="ERP", start_date=date(2026, 10, 1)),
            ana,
        )
    ).project
    epic = (
        await projects.create_epic("ti", project.id, CreateEpicCommand(name="Fase 1"), ana)
    ).epic
    return project, epic


async def task(projects, user, project, epic, name="Tarea", **kw):
    command = CreateTaskCommand(epic_id=epic.id, name=name, **kw)
    return (await projects.create_task("ti", project.id, command, user)).task


async def test_create_task_defaults_and_counts(projects, channel, ti, board) -> None:
    _, ana, *_ = ti
    project, epic = board

    t = await task(projects, ana, project, epic)

    assert (t.status, t.priority, t.position, t.version) == ("backlog", "medium", POSITION_STEP, 1)
    types = [e["type"] for e in channel.events()[-3:]]
    assert types == ["task.created", "project.updated", "epic.updated"]
    project_event, epic_event = channel.events()[-2:]
    assert (project_event["data"]["task_count"], project_event["data"]["done_count"]) == (1, 0)
    assert project_event["changed"] == ["done_count", "task_count"]
    assert project_event["data"]["version"] == project.version + 1
    assert epic_event["data"]["task_count"] == 1


async def test_payloads_match_http_responses(projects, ti, board) -> None:
    _, ana, luis, _ = ti
    project, epic = board
    t = await task(projects, ana, project, epic, owner_user_id=luis.user_id)
    [epic_view] = await projects.list_epics("ti", project.id, ana)
    [task_view] = await projects.list_tasks("ti", project.id, ana)

    assert EpicResponse.model_validate(epic_payload(epic_view)).model_dump(
        mode="json"
    ) == epic_payload(epic_view)
    assert TaskResponse.model_validate(task_payload(task_view)).model_dump(
        mode="json"
    ) == task_payload(task_view)
    assert task_view.task.id == t.id and task_view.owner.email == luis.email


async def test_status_change_goes_last_in_target_column_and_recounts(
    projects, channel, ti, board
) -> None:
    _, ana, *_ = ti
    project, epic = board
    await task(projects, ana, project, epic, "Hecha antes", status="done")
    moving = await task(projects, ana, project, epic, "Mover")

    moved = (await projects.update_task("ti", moving.id, patch(status="done"), ana)).task

    assert (moved.status, moved.position) == ("done", 2 * POSITION_STEP)
    project_event = channel.events("project.updated")[-1]
    assert (project_event["data"]["task_count"], project_event["data"]["done_count"]) == (2, 2)


async def test_status_change_between_open_columns_does_not_recount(
    projects, channel, ti, board
) -> None:
    _, ana, *_ = ti
    project, epic = board
    t = await task(projects, ana, project, epic)
    before = len(channel.events("project.updated"))

    await projects.update_task("ti", t.id, patch(status="in_progress"), ana)

    assert len(channel.events("project.updated")) == before


async def test_explicit_position_wins_over_end_of_column(projects, ti, board) -> None:
    _, ana, *_ = ti
    project, epic = board
    t = await task(projects, ana, project, epic)

    moved = await projects.update_task("ti", t.id, patch(status="in_progress", position=10.5), ana)

    assert moved.task.position == 10.5


async def test_move_between_epics_recounts_both(projects, channel, ti, board) -> None:
    _, ana, *_ = ti
    project, first = board
    second = (
        await projects.create_epic("ti", project.id, CreateEpicCommand(name="Fase 2"), ana)
    ).epic
    t = await task(projects, ana, project, first)

    moved = (await projects.update_task("ti", t.id, patch(epic_id=second.id), ana)).task

    assert (moved.epic_id, moved.position) == (second.id, t.position)
    counts = {e["data"]["id"]: e["data"]["task_count"] for e in channel.events("epic.updated")[-2:]}
    assert counts == {str(first.id): 0, str(second.id): 1}


async def test_epic_must_belong_to_the_same_project(projects, ti, board) -> None:
    _, ana, *_ = ti
    project, epic = board
    other = (
        await projects.create_project(
            "ti",
            CreateProjectCommand(folder_id=project.folder_id, name="Otro", start_date=date.today()),
            ana,
        )
    ).project
    foreign = (
        await projects.create_epic("ti", other.id, CreateEpicCommand(name="Ajeno"), ana)
    ).epic
    t = await task(projects, ana, project, epic)

    with pytest.raises(NotFoundError):
        await task(projects, ana, project, foreign)
    with pytest.raises(NotFoundError):
        await projects.update_task("ti", t.id, patch(epic_id=foreign.id), ana)
    with pytest.raises(NotFoundError):
        await projects.update_task("ti", t.id, patch(epic_id=uuid.uuid4()), ana)


@pytest.mark.parametrize(
    "kw",
    [
        {"start_date": date(2026, 10, 10), "end_date": date(2026, 10, 1)},
        {"status": "todo"},
        {"priority": "urgent"},
    ],
)
async def test_task_validations(projects, ti, board, kw) -> None:
    _, ana, *_ = ti
    project, epic = board

    with pytest.raises(InvalidInputError):
        await task(projects, ana, project, epic, **kw)


async def test_task_dates_are_optional_and_checked_together(projects, ti, board) -> None:
    _, ana, *_ = ti
    project, epic = board
    t = await task(projects, ana, project, epic, end_date=date(2026, 10, 5))

    with pytest.raises(InvalidInputError):
        await projects.update_task("ti", t.id, patch(start_date=date(2026, 10, 6)), ana)
    cleared = await projects.update_task("ti", t.id, patch(end_date=None), ana)
    assert cleared.task.end_date is None


async def test_any_member_can_delete_a_task(projects, project_repo, channel, ti, board) -> None:
    _, ana, luis, _ = ti
    project, epic = board
    t = await task(projects, ana, project, epic)

    await projects.delete_task("ti", t.id, luis)

    assert t.id not in project_repo.tasks
    deleted = channel.events("task.deleted")[-1]["data"]
    assert deleted == {"id": str(t.id), "project_id": str(project.id), "epic_id": str(epic.id)}
    assert channel.events("project.updated")[-1]["data"]["task_count"] == 0


async def test_epic_with_tasks_cannot_be_deleted(projects, ti, board) -> None:
    _, ana, *_ = ti
    project, epic = board
    t = await task(projects, ana, project, epic)

    with pytest.raises(ConflictError):
        await projects.delete_epic("ti", epic.id, ana)

    await projects.delete_task("ti", t.id, ana)
    await projects.delete_epic("ti", epic.id, ana)
    assert await projects.list_epics("ti", project.id, ana) == []


async def test_epic_delete_follows_the_manager_rule(projects, ti, board) -> None:
    _, _, luis, marta = ti
    _, epic = board

    with pytest.raises(ForbiddenError):
        await projects.delete_epic("ti", epic.id, luis)
    await projects.delete_epic("ti", epic.id, marta)


async def test_update_epic_and_reorder(projects, channel, ti, board) -> None:
    _, _, luis, _ = ti
    _, epic = board

    view = await projects.update_epic(
        "ti",
        epic.id,
        patch(UpdateEpicCommand, name="Fase uno", color="green", position=1.5),
        luis,
    )

    assert (view.epic.name, view.epic.color, view.epic.position) == ("Fase uno", "green", 1.5)
    assert view.epic.version == 2 and view.epic.updated_by == luis.email
    assert channel.events("epic.updated")[-1]["changed"] == ["color", "name", "position"]


async def test_deleting_a_project_takes_epics_and_tasks(projects, project_repo, ti, board):
    _, ana, *_ = ti
    project, epic = board
    await task(projects, ana, project, epic)

    await projects.delete_project("ti", project.id, ana)

    assert project_repo.epics == {} and project_repo.tasks == {}


async def test_project_listing_carries_task_counts(projects, ti, board) -> None:

    _, ana, *_ = ti
    project, epic = board
    await task(projects, ana, project, epic, status="done")
    await task(projects, ana, project, epic)

    [view] = await projects.list_projects("ti", ProjectFilters(), ana)

    assert (view.counts.total, view.counts.done) == (2, 1)


async def test_backlog_task_moves_to_the_board_as_the_same_record(
    projects, channel, ti, board
) -> None:
    _, ana, *_ = ti
    project, epic = board
    first = await task(projects, ana, project, epic, "Primera")
    second = await task(projects, ana, project, epic, "Segunda")
    busy = await task(projects, ana, project, epic, "En curso", status="in_progress")

    moved = (await projects.update_task("ti", first.id, patch(status="in_progress"), ana)).task

    assert moved.id == first.id and moved.status == "in_progress"
    assert moved.position == busy.position + POSITION_STEP
    # The backlog keeps its own order: the remaining task did not move.
    assert (second.status, second.position) == ("backlog", 2 * POSITION_STEP)
    assert channel.events("task.updated")[-1]["changed"] == ["status"]


async def test_task_without_epic_is_allowed_and_counted_on_the_project(
    projects, channel, ti, board
) -> None:
    _, ana, *_ = ti
    project, _ = board

    t = (await projects.create_task("ti", project.id, CreateTaskCommand(name="Suelta"), ana)).task

    assert t.epic_id is None
    assert task_payload(await _view(projects, ana, project, t))["epic_id"] is None
    assert [e["type"] for e in channel.events()[-2:]] == ["task.created", "project.updated"]
    assert channel.events("project.updated")[-1]["data"]["task_count"] == 1

    done = await projects.update_task("ti", t.id, patch(status="done"), ana)
    assert channel.events("project.updated")[-1]["data"]["done_count"] == 1
    assert done.task.epic_id is None
    await projects.delete_task("ti", t.id, ana)
    assert channel.events("task.deleted")[-1]["data"]["epic_id"] is None
    assert channel.events("project.updated")[-1]["data"]["task_count"] == 0


async def _view(projects, user, project, t):
    return next(v for v in await projects.list_tasks("ti", project.id, user) if v.task.id == t.id)


async def test_task_can_be_attached_to_and_detached_from_an_epic(
    projects, channel, ti, board
) -> None:
    _, ana, *_ = ti
    project, epic = board
    t = (await projects.create_task("ti", project.id, CreateTaskCommand(name="Suelta"), ana)).task

    attached = (await projects.update_task("ti", t.id, patch(epic_id=epic.id), ana)).task
    assert attached.epic_id == epic.id
    assert channel.events("epic.updated")[-1]["data"]["task_count"] == 1

    detached = (await projects.update_task("ti", t.id, patch(epic_id=None), ana)).task
    assert detached.epic_id is None
    assert channel.events("epic.updated")[-1]["data"]["task_count"] == 0


async def test_blocked_reason_lives_only_while_blocked(projects, ti, board) -> None:
    _, ana, *_ = ti
    project, epic = board
    t = await task(projects, ana, project, epic, status="in_progress", blocked_reason="ignorada")
    assert t.blocked_reason is None

    blocked = await projects.update_task(
        "ti", t.id, patch(status="blocked", blocked_reason="  Espera al proveedor "), ana
    )
    assert (blocked.task.status, blocked.task.blocked_reason) == ("blocked", "Espera al proveedor")

    edited = await projects.update_task("ti", t.id, patch(blocked_reason="Sin acceso"), ana)
    assert edited.task.blocked_reason == "Sin acceso"

    resumed = await projects.update_task("ti", t.id, patch(status="in_progress"), ana)
    assert (resumed.task.status, resumed.task.blocked_reason) == ("in_progress", None)

    with pytest.raises(InvalidInputError):
        await projects.update_task(
            "ti", t.id, patch(status="blocked", blocked_reason="x" * 501), ana
        )


async def test_task_payload_matches_the_response_with_blocked_reason(projects, ti, board) -> None:
    _, ana, *_ = ti
    project, _ = board
    t = (
        await projects.create_task(
            "ti",
            project.id,
            CreateTaskCommand(name="Bloqueada", status="blocked", blocked_reason="Falta permiso"),
            ana,
        )
    ).task

    payload = task_payload(await _view(projects, ana, project, t))

    assert payload["blocked_reason"] == "Falta permiso"
    assert TaskResponse.model_validate(payload).model_dump(mode="json") == payload
