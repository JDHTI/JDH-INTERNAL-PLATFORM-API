"""HTTP and WebSocket endpoints of the per-area project manager.

Mounted under ``/areas``. Every write is also pushed to
``WS /areas/{slug}/projects/live`` with the same JSON shape as the HTTP
responses, so the UI can apply it without refetching.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import date, datetime
from typing import Any
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.core.errors import ForbiddenError, NotFoundError
from app.core.logging import logger
from app.modules.identity.application.service import IdentityService
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import (
    get_current_principal,
    websocket_origin_allowed,
    websocket_principal,
)
from app.modules.projects.application.dto import (
    CreateEpicCommand,
    CreateProjectCommand,
    CreateTaskCommand,
    EpicView,
    FolderCommand,
    FolderView,
    ProjectView,
    TaskView,
    UpdateEpicCommand,
    UpdateProjectCommand,
    UpdateTaskCommand,
    epic_payload,
    folder_payload,
    project_payload,
    task_payload,
)
from app.modules.projects.application.live import LiveSession, ProjectLiveService
from app.modules.projects.application.service import ProjectService
from app.modules.projects.domain.entities import COLORS, PRIORITIES, STATUSES, TASK_STATUSES
from app.modules.projects.domain.ports import ProjectFilters

area_router = APIRouter()

_principal = Depends(get_current_principal)
_service = Depends(Provide[ApplicationContainer.v1_container.project_service])
_live = Depends(Provide[ApplicationContainer.v1_container.project_live_service])
_identity = Depends(Provide[ApplicationContainer.v1_container.identity_service])

_STATUS_LABELS = {
    "created": "Creado",
    "ideation": "En ideación",
    "in_progress": "En progreso",
    "on_hold": "En pausa",
    "completed": "Finalizado",
    "cancelled": "Cancelado",
}
_TASK_STATUS_LABELS = {
    "backlog": "Backlog",
    "in_progress": "En curso",
    "pending_validation": "Pendiente de validación",
    "blocked": "Bloqueado",
    "done": "Hecha",
}
_PRIORITY_LABELS = {"low": "Baja", "medium": "Media", "high": "Alta"}
_COLOR_LABELS = {
    "default": "Predeterminado",
    "gray": "Gris",
    "brown": "Marrón",
    "orange": "Naranja",
    "yellow": "Amarillo",
    "green": "Verde",
    "blue": "Azul",
    "purple": "Morado",
    "pink": "Rosado",
    "red": "Rojo",
}

# WebSocket close codes (4000-4999 are free for applications).
_WS_UNAUTHENTICATED = 4401
_WS_FORBIDDEN = 4403
_WS_NOT_FOUND = 4404
_WS_IDLE = 4408


# --- Schemas ---


class FolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=1000)
    color: str | None = Field(default=None, examples=["blue"])


class FolderUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=1000)
    color: str | None = None


class FolderResponse(BaseModel):
    id: UUID
    area_id: UUID
    name: str
    description: str | None
    color: str
    project_count: int
    created_by: str
    created_at: datetime
    updated_at: datetime


class PersonResponse(BaseModel):
    id: UUID
    email: str | None
    display_name: str | None


class ProjectRequest(BaseModel):
    folder_id: UUID
    name: str = Field(min_length=1, max_length=200)
    start_date: date
    end_date: date | None = None
    description: str | None = Field(default=None, max_length=5000)
    color: str | None = None
    status: str | None = Field(default=None, description="Defaults to `created`")
    priority: str | None = Field(default=None, description="Defaults to `medium`")
    owner_user_id: UUID | None = None


class ProjectUpdateRequest(BaseModel):
    """Send only what changes; ``null`` clears description, end date and owner."""

    folder_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    color: str | None = None
    status: str | None = None
    priority: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    owner_user_id: UUID | None = None
    position: float | None = Field(
        default=None, description="Midpoint between the neighbours where it was dropped"
    )


class ProjectResponse(BaseModel):
    id: UUID
    area_id: UUID
    folder_id: UUID
    name: str
    description: str | None
    color: str
    status: str
    priority: str
    start_date: date
    end_date: date | None
    owner: PersonResponse | None
    position: float
    is_archived: bool
    archived_at: datetime | None
    task_count: int
    done_count: int
    version: int
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


class EpicRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    color: str | None = None
    start_date: date | None = None
    end_date: date | None = None


class EpicUpdateRequest(BaseModel):
    """Send only what changes; ``null`` clears description and dates."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    color: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    position: float | None = None


class EpicResponse(BaseModel):
    id: UUID
    area_id: UUID
    project_id: UUID
    name: str
    description: str | None
    color: str
    start_date: date | None
    end_date: date | None
    position: float
    task_count: int
    done_count: int
    version: int
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


class TaskRequest(BaseModel):
    epic_id: UUID | None = None
    name: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=10000)
    status: str | None = Field(default=None, description="Defaults to `backlog`")
    priority: str | None = Field(default=None, description="Defaults to `medium`")
    owner_user_id: UUID | None = None
    start_date: date | None = None
    end_date: date | None = None
    blocked_reason: str | None = Field(default=None, max_length=500)


class TaskUpdateRequest(BaseModel):
    """Send only what changes; ``null`` clears description, dates and owner.

    A new ``status`` without ``position`` moves the task last in that column
    (leaving ``backlog`` is just this). ``epic_id: null`` detaches the task from
    its epic. ``blocked_reason`` only sticks while the status is ``blocked``.
    """

    epic_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=10000)
    status: str | None = None
    priority: str | None = None
    owner_user_id: UUID | None = None
    start_date: date | None = None
    end_date: date | None = None
    position: float | None = None
    blocked_reason: str | None = Field(default=None, max_length=500)


class TaskResponse(BaseModel):
    id: UUID
    area_id: UUID
    project_id: UUID
    epic_id: UUID | None
    name: str
    description: str | None
    status: str
    priority: str
    owner: PersonResponse | None
    start_date: date | None
    end_date: date | None
    position: float
    blocked_reason: str | None
    version: int
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


class OptionResponse(BaseModel):
    value: str
    label: str


class AccessResponse(BaseModel):
    email: str
    can_delete_any: bool


class CatalogResponse(BaseModel):
    statuses: list[OptionResponse]
    task_statuses: list[OptionResponse]
    priorities: list[OptionResponse]
    colors: list[OptionResponse]


def _folder(view: FolderView) -> FolderResponse:
    return FolderResponse.model_validate(folder_payload(view))


def _project(view: ProjectView) -> ProjectResponse:
    return ProjectResponse.model_validate(project_payload(view))


def _epic(view: EpicView) -> EpicResponse:
    return EpicResponse.model_validate(epic_payload(view))


def _task(view: TaskView) -> TaskResponse:
    return TaskResponse.model_validate(task_payload(view))


def _options(values: tuple[str, ...], labels: dict[str, str]) -> list[OptionResponse]:
    return [OptionResponse(value=v, label=labels[v]) for v in values]


def _split(raw: str | None) -> tuple[str, ...]:
    return tuple(s for s in (part.strip() for part in (raw or "").split(",")) if s)


# --- Catalog ---


@area_router.get(
    "/{slug}/projects/catalog",
    response_model=CatalogResponse,
    summary="Statuses, priorities and colors with their labels",
)
@inject
async def catalog(
    slug: str, principal: Principal = _principal, service: ProjectService = _service
) -> CatalogResponse:
    await service.authorize(slug, principal)
    return CatalogResponse(
        statuses=_options(STATUSES, _STATUS_LABELS),
        task_statuses=_options(TASK_STATUSES, _TASK_STATUS_LABELS),
        priorities=_options(PRIORITIES, _PRIORITY_LABELS),
        colors=_options(COLORS, _COLOR_LABELS),
    )


@area_router.get(
    "/{slug}/projects/access",
    response_model=AccessResponse,
    summary="What the caller may delete",
    description=(
        "An item can be deleted when `can_delete_any` is true or its `created_by` "
        "equals `email` (case-insensitive)."
    ),
)
@inject
async def access(
    slug: str, principal: Principal = _principal, service: ProjectService = _service
) -> AccessResponse:
    return AccessResponse(
        email=principal.email, can_delete_any=await service.can_delete_any(slug, principal)
    )


@area_router.get(
    "/{slug}/projects/people",
    response_model=list[PersonResponse],
    summary="Search active users to make responsible",
)
@inject
async def search_people(
    slug: str,
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=20, ge=1, le=50),
    principal: Principal = _principal,
    service: ProjectService = _service,
) -> list[PersonResponse]:
    users = await service.search_people(slug, q, principal, limit)
    return [PersonResponse(id=u.id, email=u.email, display_name=u.display_name) for u in users]


# --- Folders ---


@area_router.get(
    "/{slug}/project-folders",
    response_model=list[FolderResponse],
    summary="Project folders of an area",
)
@inject
async def list_folders(
    slug: str, principal: Principal = _principal, service: ProjectService = _service
) -> list[FolderResponse]:
    return [_folder(v) for v in await service.list_folders(slug, principal)]


@area_router.post(
    "/{slug}/project-folders",
    response_model=FolderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create project folder",
)
@inject
async def create_folder(
    slug: str,
    payload: FolderRequest,
    principal: Principal = _principal,
    service: ProjectService = _service,
) -> FolderResponse:
    command = FolderCommand(name=payload.name, description=payload.description, color=payload.color)
    return _folder(await service.create_folder(slug, command, principal))


@area_router.patch(
    "/{slug}/project-folders/{folder_id}",
    response_model=FolderResponse,
    summary="Update project folder",
)
@inject
async def update_folder(
    slug: str,
    folder_id: UUID,
    payload: FolderUpdateRequest,
    principal: Principal = _principal,
    service: ProjectService = _service,
) -> FolderResponse:
    command = FolderCommand(fields=frozenset(payload.model_fields_set), **payload.model_dump())
    return _folder(await service.update_folder(slug, folder_id, command, principal))


@area_router.delete(
    "/{slug}/project-folders/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete empty project folder",
)
@inject
async def delete_folder(
    slug: str,
    folder_id: UUID,
    principal: Principal = _principal,
    service: ProjectService = _service,
) -> None:
    await service.delete_folder(slug, folder_id, principal)


# --- Projects ---


@area_router.get("/{slug}/projects", response_model=list[ProjectResponse], summary="Projects")
@inject
async def list_projects(
    slug: str,
    folder_id: UUID | None = None,
    status_filter: str | None = Query(
        default=None, alias="status", description="Comma-separated statuses"
    ),
    include_archived: bool = False,
    principal: Principal = _principal,
    service: ProjectService = _service,
) -> list[ProjectResponse]:
    filters = ProjectFilters(
        folder_id=folder_id, statuses=_split(status_filter), include_archived=include_archived
    )
    return [_project(v) for v in await service.list_projects(slug, filters, principal)]


@area_router.post(
    "/{slug}/projects",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create project",
)
@inject
async def create_project(
    slug: str,
    payload: ProjectRequest,
    principal: Principal = _principal,
    service: ProjectService = _service,
) -> ProjectResponse:
    command = CreateProjectCommand(**payload.model_dump())
    return _project(await service.create_project(slug, command, principal))


@area_router.get(
    "/{slug}/projects/{project_id}", response_model=ProjectResponse, summary="Project detail"
)
@inject
async def get_project(
    slug: str,
    project_id: UUID,
    principal: Principal = _principal,
    service: ProjectService = _service,
) -> ProjectResponse:
    return _project(await service.get_project(slug, project_id, principal))


@area_router.patch(
    "/{slug}/projects/{project_id}", response_model=ProjectResponse, summary="Update project"
)
@inject
async def update_project(
    slug: str,
    project_id: UUID,
    payload: ProjectUpdateRequest,
    principal: Principal = _principal,
    service: ProjectService = _service,
) -> ProjectResponse:
    command = UpdateProjectCommand(
        fields=frozenset(payload.model_fields_set), **payload.model_dump()
    )
    return _project(await service.update_project(slug, project_id, command, principal))


@area_router.post(
    "/{slug}/projects/{project_id}/archive",
    response_model=ProjectResponse,
    summary="Archive project",
)
@inject
async def archive_project(
    slug: str,
    project_id: UUID,
    principal: Principal = _principal,
    service: ProjectService = _service,
) -> ProjectResponse:
    return _project(await service.set_archived(slug, project_id, True, principal))


@area_router.post(
    "/{slug}/projects/{project_id}/restore",
    response_model=ProjectResponse,
    summary="Restore archived project",
)
@inject
async def restore_project(
    slug: str,
    project_id: UUID,
    principal: Principal = _principal,
    service: ProjectService = _service,
) -> ProjectResponse:
    return _project(await service.set_archived(slug, project_id, False, principal))


@area_router.delete(
    "/{slug}/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete project permanently",
)
@inject
async def delete_project(
    slug: str,
    project_id: UUID,
    principal: Principal = _principal,
    service: ProjectService = _service,
) -> None:
    await service.delete_project(slug, project_id, principal)


# --- epics ---


@area_router.get(
    "/{slug}/projects/{project_id}/epics",
    response_model=list[EpicResponse],
    summary="epics of a project",
)
@inject
async def list_epics(
    slug: str,
    project_id: UUID,
    principal: Principal = _principal,
    service: ProjectService = _service,
) -> list[EpicResponse]:
    return [_epic(v) for v in await service.list_epics(slug, project_id, principal)]


@area_router.post(
    "/{slug}/projects/{project_id}/epics",
    response_model=EpicResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create epic",
)
@inject
async def create_epic(
    slug: str,
    project_id: UUID,
    payload: EpicRequest,
    principal: Principal = _principal,
    service: ProjectService = _service,
) -> EpicResponse:
    command = CreateEpicCommand(**payload.model_dump())
    return _epic(await service.create_epic(slug, project_id, command, principal))


@area_router.patch(
    "/{slug}/project-epics/{epic_id}", response_model=EpicResponse, summary="Update epic"
)
@inject
async def update_epic(
    slug: str,
    epic_id: UUID,
    payload: EpicUpdateRequest,
    principal: Principal = _principal,
    service: ProjectService = _service,
) -> EpicResponse:
    command = UpdateEpicCommand(fields=frozenset(payload.model_fields_set), **payload.model_dump())
    return _epic(await service.update_epic(slug, epic_id, command, principal))


@area_router.delete(
    "/{slug}/project-epics/{epic_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete empty epic",
)
@inject
async def delete_epic(
    slug: str,
    epic_id: UUID,
    principal: Principal = _principal,
    service: ProjectService = _service,
) -> None:
    await service.delete_epic(slug, epic_id, principal)


# --- Tasks ---


@area_router.get(
    "/{slug}/projects/{project_id}/tasks",
    response_model=list[TaskResponse],
    summary="Tasks of a project's board",
)
@inject
async def list_tasks(
    slug: str,
    project_id: UUID,
    principal: Principal = _principal,
    service: ProjectService = _service,
) -> list[TaskResponse]:
    return [_task(v) for v in await service.list_tasks(slug, project_id, principal)]


@area_router.post(
    "/{slug}/projects/{project_id}/tasks",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create task",
)
@inject
async def create_task(
    slug: str,
    project_id: UUID,
    payload: TaskRequest,
    principal: Principal = _principal,
    service: ProjectService = _service,
) -> TaskResponse:
    command = CreateTaskCommand(**payload.model_dump())
    return _task(await service.create_task(slug, project_id, command, principal))


@area_router.patch(
    "/{slug}/project-tasks/{task_id}", response_model=TaskResponse, summary="Update task"
)
@inject
async def update_task(
    slug: str,
    task_id: UUID,
    payload: TaskUpdateRequest,
    principal: Principal = _principal,
    service: ProjectService = _service,
) -> TaskResponse:
    command = UpdateTaskCommand(fields=frozenset(payload.model_fields_set), **payload.model_dump())
    return _task(await service.update_task(slug, task_id, command, principal))


@area_router.delete(
    "/{slug}/project-tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete task",
)
@inject
async def delete_task(
    slug: str,
    task_id: UUID,
    principal: Principal = _principal,
    service: ProjectService = _service,
) -> None:
    await service.delete_task(slug, task_id, principal)


# --- Live channel ---


async def _pump_events(websocket: WebSocket, session: LiveSession, lock: asyncio.Lock) -> None:
    while True:
        message = await session.next_message()
        async with lock:
            await websocket.send_json(message)


async def _read_client(
    websocket: WebSocket, session: LiveSession, lock: asyncio.Lock, idle_seconds: int
) -> None:
    while True:
        try:
            message: Any = await asyncio.wait_for(websocket.receive_json(), idle_seconds)
        except TimeoutError:
            await websocket.close(code=_WS_IDLE, reason="Sin actividad")
            return
        except ValueError:
            continue  # not JSON; ignore rather than drop the connection
        if not isinstance(message, dict):
            continue
        kind = message.get("type")
        if kind == "ping":
            await session.heartbeat()
            async with lock:
                await websocket.send_json({"type": "pong"})
        elif kind == "focus":
            await session.focus(message.get("folder_id"), message.get("project_id"))


@area_router.websocket("/{slug}/projects/live")
@inject
async def live(
    websocket: WebSocket,
    slug: str,
    service: ProjectService = _service,
    live_service: ProjectLiveService = _live,
    identity: IdentityService = _identity,
) -> None:
    """Live changes and presence of an area's projects.

    Server → client: ``hello`` (connection id and members), ``folder.*`` and
    ``project.*``, ``epic.*`` and ``task.*`` events,``presence`` (members), ``pong`` and
    ``sync.required`` (refetch: events may have been missed).
    Client → server: ``{"type": "ping"}`` every ~20 s and
    ``{"type": "focus", "folder_id": ..., "project_id": ...}`` on navigation.
    """

    if not websocket_origin_allowed(websocket, identity.settings.cors_origins):
        await websocket.close(code=_WS_FORBIDDEN, reason="Origen no permitido")
        return
    # Accept before refusing: a close sent during the handshake reaches the
    # browser as a bare 1006, and the UI needs the code to tell why.
    await websocket.accept()
    principal = await websocket_principal(websocket, identity)
    if principal is None:
        await websocket.close(code=_WS_UNAUTHENTICATED, reason="Autenticación requerida")
        return
    try:
        area = await service.authorize(slug, principal)
    except ForbiddenError as exc:
        await websocket.close(code=_WS_FORBIDDEN, reason=str(exc))
        return
    except NotFoundError as exc:
        await websocket.close(code=_WS_NOT_FOUND, reason=str(exc))
        return

    lock = asyncio.Lock()
    try:
        async with live_service.join(area.id, principal) as session:
            async with lock:
                await websocket.send_json(
                    {
                        "type": "hello",
                        "area_id": str(area.id),
                        "connection_id": session.connection_id,
                        "members": await session.members(),
                        "presence_ttl": live_service.presence_ttl,
                    }
                )
            tasks = {
                asyncio.create_task(_pump_events(websocket, session, lock)),
                asyncio.create_task(
                    _read_client(websocket, session, lock, live_service.presence_ttl)
                ),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in pending:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            for task in done:
                exc = task.exception()
                if exc is not None and not isinstance(exc, WebSocketDisconnect):
                    raise exc
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 - one broken socket must not bubble up
        logger.error("Project live connection for %s ended: %s", principal.email, exc)
        with contextlib.suppress(Exception):
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)


__all__ = ["area_router"]
