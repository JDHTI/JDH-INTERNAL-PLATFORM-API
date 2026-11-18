"""What the action plans need from the outside."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from app.modules.action_plans.domain.entities import ActionPlan, PlanEvent, PlanFile, Process
from app.modules.identity.domain.principal import Principal


@dataclass(frozen=True)
class PlanFilters:
    """Filters of the matrix; ``visible_to`` limits to the actions of a responsible."""

    origin: str | None = None
    process_id: UUID | None = None
    action_type: str | None = None
    status: str | None = None
    effectiveness: str | None = None
    city: str | None = None
    year: int | None = None
    month: int | None = None
    case_id: UUID | None = None
    query: str | None = None
    visible_to: UUID | None = None
    overdue_on: date | None = None


class ActionPlanRepository(Protocol):
    """Actions with their children, the process catalog, files and history."""

    async def list_processes(self, include_inactive: bool = False) -> list[Process]:
        """The processes of the SIG, by code."""
        ...

    async def get_process(self, process_id: UUID) -> Process | None:
        """One process."""
        ...

    async def get_process_by_code(self, code: str) -> Process | None:
        """A process by its code (ALM)."""
        ...

    async def save_process(self, process: Process) -> Process:
        """Insert or update a process; a repeated code is a conflict."""
        ...

    async def next_number(self) -> int:
        """The number the next action takes (the highest one plus one)."""
        ...

    async def create(self, plan: ActionPlan, event: PlanEvent) -> ActionPlan:
        """Insert an action with its children and its first event."""
        ...

    async def save(self, plan: ActionPlan, events: Sequence[PlanEvent]) -> ActionPlan:
        """Rewrite an action and its children, and add events, in one transaction."""
        ...

    async def get(self, plan_id: UUID) -> ActionPlan | None:
        """One action with its children."""
        ...

    async def code_exists(self, code: str) -> bool:
        """Whether an action already has this code (the import is idempotent by it)."""
        ...

    async def list_plans(
        self, filters: PlanFilters, offset: int, limit: int
    ) -> tuple[list[ActionPlan], int]:
        """A page of actions (newest first) and the total."""
        ...

    async def all_plans(self, filters: PlanFilters) -> list[ActionPlan]:
        """Every action of the filters, for the indicators and the export."""
        ...

    async def similar(self, reporter_nit: str, cause_label: str | None, exclude: UUID) -> int:
        """Earlier actions of the same customer (and cause, when known)."""
        ...

    async def delete(self, plan_id: UUID) -> list[str]:
        """Delete an action; returns the storage keys of its files."""
        ...

    async def add_event(self, event: PlanEvent) -> None:
        """Append to the history."""
        ...

    async def list_events(self, plan_id: UUID) -> list[PlanEvent]:
        """The history, oldest first."""
        ...

    async def add_file(self, file: PlanFile) -> None:
        """Record an evidence file."""
        ...

    async def list_files(self, plan_id: UUID) -> list[PlanFile]:
        """The evidence of an action."""
        ...

    async def get_file(self, plan_id: UUID, file_id: UUID) -> PlanFile | None:
        """One evidence file."""
        ...

    async def delete_file(self, file_id: UUID) -> None:
        """Forget an evidence file."""
        ...


class ModulePolicy(Protocol):
    """Who reads and manages the module ``planes-accion``."""

    async def is_manager(self, principal: Principal) -> bool:
        """``manage`` on the module."""
        ...

    async def can_read(self, principal: Principal) -> bool:
        """``read`` or ``manage`` on the module."""
        ...


@dataclass(frozen=True)
class CaseFacts:
    """What an action takes from a PQRSF case."""

    id: UUID
    code: str
    received_on: date
    customer_nit: str | None
    customer_name: str | None
    requester_name: str | None
    city: str | None
    process_code: str | None
    subject: str | None
    description: str | None
    cause_label: str | None
    has_photos: bool
    pickup_order: str | None
    om_codes: tuple[str, ...]


class CaseSource(Protocol):
    """The PQRSF case an action comes from (implemented over the PQRSF services)."""

    async def facts(self, case_id: UUID, principal: Principal) -> CaseFacts:
        """The case, when the caller may work it."""
        ...

    async def link(
        self, case_id: UUID, principal: Principal, plan_id: UUID, plan_code: str
    ) -> None:
        """Leave the action in the case timeline."""
        ...


class Profiles(Protocol):
    """Name and position of a platform user (the directory)."""

    async def signer(self, user_id: UUID):  # noqa: ANN201 - name / position / email / phone
        """The person as the directory shows them, or ``None``."""
        ...

    async def names(self, user_ids: Sequence[UUID]) -> dict[UUID, str]:
        """Name of each user that exists (the email when it has none)."""
        ...


class Bell(Protocol):
    """The intranet bell (and Web Push)."""

    async def ring(
        self, user_ids: list[UUID], key: str, title: str, summary: str, path: str
    ) -> None:
        """One notice for these users."""
        ...


class Storage(Protocol):
    """The private bucket."""

    async def put_object(self, key: str, body: bytes, content_type: str | None = None) -> None:
        """Store bytes."""
        ...

    async def get_object(self, key: str) -> bytes:
        """Read bytes."""
        ...

    async def delete_object(self, key: str) -> None:
        """Delete an object."""
        ...


class FileGuard(Protocol):
    """The upload rules of the platform (extensions, size, antivirus)."""

    max_attachment_bytes: int

    def refuse(self, filename: str, size: int, count: int, total: int) -> str | None:
        """Why a file is refused before reading it, or ``None``."""
        ...

    async def clean(self, filename: str, content: bytes) -> tuple[str, str, str, bytes]:
        """``(name, extension, content type, bytes)`` of an accepted file."""
        ...
