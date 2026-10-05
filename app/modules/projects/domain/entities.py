"""Project management entities and their pure rules.

Depth mirrors the document manager: area / folder / project; folders have no
parent. Colors are palette keys, not hex: the UI maps each key to a light and a
dark shade, so every project stays readable in both themes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from app.core.errors import InvalidInputError

STATUSES = ("created", "ideation", "in_progress", "on_hold", "completed", "cancelled")
PRIORITIES = ("low", "medium", "high")
COLORS = (
    "default",
    "gray",
    "brown",
    "orange",
    "yellow",
    "green",
    "blue",
    "purple",
    "pink",
    "red",
)

# ``backlog`` lives on its own board; the other four are the main board columns.
TASK_STATUSES = ("backlog", "in_progress", "pending_validation", "blocked", "done")
DONE_TASK_STATUS = "done"
BLOCKED_TASK_STATUS = "blocked"
DEFAULT_TASK_STATUS = "backlog"
BLOCKED_REASON_MAX = 500

DEFAULT_STATUS = "created"
DEFAULT_PRIORITY = "medium"
DEFAULT_COLOR = "default"

# Gap left between consecutive positions so a drop between two projects can
# take the midpoint without renumbering the folder.
POSITION_STEP = 1024.0


@dataclass
class ProjectFolder:
    id: UUID
    area_id: UUID
    name: str
    description: str | None
    color: str
    created_by: str
    created_at: datetime
    updated_at: datetime


@dataclass
class Project:
    """``version`` grows on every write; clients drop events older than what they hold."""

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
    owner_user_id: UUID | None
    position: float
    is_archived: bool
    archived_at: datetime | None
    version: int
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


@dataclass
class Epic:
    """Group of tasks inside a project; ``version`` also grows when its counts change."""

    id: UUID
    area_id: UUID
    project_id: UUID
    name: str
    description: str | None
    color: str
    start_date: date | None
    end_date: date | None
    position: float
    version: int
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


@dataclass
class Task:
    """One card of a project's board or backlog; ``position`` orders it inside its status column.

    ``epic_id`` is optional (epics only group tasks) and ``blocked_reason`` is
    set only while the status is ``blocked``.
    """

    id: UUID
    area_id: UUID
    project_id: UUID
    epic_id: UUID | None
    name: str
    description: str | None
    status: str
    priority: str
    owner_user_id: UUID | None
    start_date: date | None
    end_date: date | None
    position: float
    blocked_reason: str | None
    version: int
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TaskCounts:
    total: int = 0
    done: int = 0


def clean_text(value: str, field: str, max_length: int) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise InvalidInputError(f"El {field} no puede estar vacío")
    if len(cleaned) > max_length:
        raise InvalidInputError(f"El {field} admite máximo {max_length} caracteres")
    return cleaned


def optional_text(value: str | None, field: str, max_length: int) -> str | None:
    """Like ``clean_text`` but keeps line breaks and turns blank into ``None``."""

    if value is None or not value.strip():
        return None
    value = value.strip()
    if len(value) > max_length:
        raise InvalidInputError(f"La {field} admite máximo {max_length} caracteres")
    return value


def clean_blocked_reason(status: str, reason: str | None) -> str | None:
    """The reason of a blocked task; any other status carries none."""

    if status != BLOCKED_TASK_STATUS:
        return None
    return optional_text(reason, "razón del bloqueo", BLOCKED_REASON_MAX)


def _choice(value: str, allowed: tuple[str, ...], field: str) -> str:
    normalized = value.strip().lower()
    if normalized not in allowed:
        raise InvalidInputError(f"{field} no válido; usa uno de: {', '.join(allowed)}")
    return normalized


def normalize_status(value: str) -> str:
    return _choice(value, STATUSES, "Estado")


def normalize_task_status(value: str) -> str:
    return _choice(value, TASK_STATUSES, "Estado de la tarea")


def normalize_priority(value: str) -> str:
    return _choice(value, PRIORITIES, "Prioridad")


def normalize_color(value: str) -> str:
    return _choice(value, COLORS, "Color")


def check_dates(start_date: date | None, end_date: date | None) -> None:
    if start_date is not None and end_date is not None and end_date < start_date:
        raise InvalidInputError("La fecha de fin no puede ser anterior a la fecha de inicio")


def check_position(position: float) -> float:
    # NaN and infinities would break ordering for everyone watching the folder.
    if position != position or position in (float("inf"), float("-inf")):
        raise InvalidInputError("La posición debe ser un número finito")
    return float(position)
