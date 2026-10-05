"""Commands, results and the JSON shape shared by HTTP responses and live events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID

from app.modules.identity.domain.principal import DirectoryUser, Principal
from app.modules.projects.domain.entities import Epic, Project, ProjectFolder, Task, TaskCounts


@dataclass(frozen=True)
class FolderCommand:
    """Fields of a folder; on update only those listed in ``fields`` apply."""

    name: str | None = None
    description: str | None = None
    color: str | None = None
    fields: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class CreateProjectCommand:
    folder_id: UUID
    name: str
    start_date: date
    end_date: date | None = None
    description: str | None = None
    color: str | None = None
    status: str | None = None
    priority: str | None = None
    owner_user_id: UUID | None = None


@dataclass(frozen=True)
class UpdateProjectCommand:
    """Only the attributes listed in ``fields`` apply; ``None`` clears optional ones."""

    fields: frozenset[str]
    folder_id: UUID | None = None
    name: str | None = None
    description: str | None = None
    color: str | None = None
    status: str | None = None
    priority: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    owner_user_id: UUID | None = None
    position: float | None = None


@dataclass(frozen=True)
class CreateEpicCommand:
    name: str
    description: str | None = None
    color: str | None = None
    start_date: date | None = None
    end_date: date | None = None


@dataclass(frozen=True)
class UpdateEpicCommand:
    """Only the attributes listed in ``fields`` apply; ``None`` clears optional ones."""

    fields: frozenset[str]
    name: str | None = None
    description: str | None = None
    color: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    position: float | None = None


@dataclass(frozen=True)
class CreateTaskCommand:
    name: str
    epic_id: UUID | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    owner_user_id: UUID | None = None
    start_date: date | None = None
    end_date: date | None = None
    blocked_reason: str | None = None


@dataclass(frozen=True)
class UpdateTaskCommand:
    """Only the attributes listed in ``fields`` apply; ``None`` clears optional ones."""

    fields: frozenset[str]
    epic_id: UUID | None = None
    name: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    owner_user_id: UUID | None = None
    start_date: date | None = None
    end_date: date | None = None
    position: float | None = None
    blocked_reason: str | None = None


@dataclass(frozen=True)
class FolderView:
    folder: ProjectFolder
    project_count: int


@dataclass(frozen=True)
class ProjectView:
    project: Project
    owner: DirectoryUser | None
    counts: TaskCounts = TaskCounts()


@dataclass(frozen=True)
class EpicView:
    epic: Epic
    counts: TaskCounts = TaskCounts()


@dataclass(frozen=True)
class TaskView:
    task: Task
    owner: DirectoryUser | None


def iso(moment: datetime | None) -> str | None:
    """ISO 8601 with ``Z`` for UTC, the way the HTTP responses render it."""

    if moment is None:
        return None
    return moment.isoformat().replace("+00:00", "Z")


def person_payload(user: DirectoryUser | None) -> dict[str, Any] | None:
    if user is None:
        return None
    return {"id": str(user.id), "email": user.email, "display_name": user.display_name}


def actor_payload(principal: Principal) -> dict[str, Any]:
    return {
        "user_id": str(principal.user_id),
        "email": principal.email,
        "display_name": principal.display_name,
    }


def folder_payload(view: FolderView) -> dict[str, Any]:
    folder = view.folder
    return {
        "id": str(folder.id),
        "area_id": str(folder.area_id),
        "name": folder.name,
        "description": folder.description,
        "color": folder.color,
        "project_count": view.project_count,
        "created_by": folder.created_by,
        "created_at": iso(folder.created_at),
        "updated_at": iso(folder.updated_at),
    }


def project_payload(view: ProjectView) -> dict[str, Any]:
    p = view.project
    return {
        "id": str(p.id),
        "area_id": str(p.area_id),
        "folder_id": str(p.folder_id),
        "name": p.name,
        "description": p.description,
        "color": p.color,
        "status": p.status,
        "priority": p.priority,
        "start_date": p.start_date.isoformat(),
        "end_date": p.end_date.isoformat() if p.end_date else None,
        "owner": person_payload(view.owner),
        "position": p.position,
        "is_archived": p.is_archived,
        "archived_at": iso(p.archived_at),
        "task_count": view.counts.total,
        "done_count": view.counts.done,
        "version": p.version,
        "created_by": p.created_by,
        "updated_by": p.updated_by,
        "created_at": iso(p.created_at),
        "updated_at": iso(p.updated_at),
    }


def _day(value: date | None) -> str | None:
    return value.isoformat() if value else None


def epic_payload(view: EpicView) -> dict[str, Any]:
    b = view.epic
    return {
        "id": str(b.id),
        "area_id": str(b.area_id),
        "project_id": str(b.project_id),
        "name": b.name,
        "description": b.description,
        "color": b.color,
        "start_date": _day(b.start_date),
        "end_date": _day(b.end_date),
        "position": b.position,
        "task_count": view.counts.total,
        "done_count": view.counts.done,
        "version": b.version,
        "created_by": b.created_by,
        "updated_by": b.updated_by,
        "created_at": iso(b.created_at),
        "updated_at": iso(b.updated_at),
    }


def task_payload(view: TaskView) -> dict[str, Any]:
    t = view.task
    return {
        "id": str(t.id),
        "area_id": str(t.area_id),
        "project_id": str(t.project_id),
        "epic_id": str(t.epic_id) if t.epic_id else None,
        "name": t.name,
        "description": t.description,
        "status": t.status,
        "priority": t.priority,
        "owner": person_payload(view.owner),
        "start_date": _day(t.start_date),
        "end_date": _day(t.end_date),
        "position": t.position,
        "blocked_reason": t.blocked_reason,
        "version": t.version,
        "created_by": t.created_by,
        "updated_by": t.updated_by,
        "created_at": iso(t.created_at),
        "updated_at": iso(t.updated_at),
    }
