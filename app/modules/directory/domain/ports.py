"""Contracts the directory use cases need from the outside."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from app.modules.directory.domain.entities import (
    Employee,
    GraphPhoto,
    GraphPhotoMeta,
    GraphUser,
    RunStatus,
    RunTrigger,
    ScheduleSlot,
    Site,
    SyncRun,
)


class DirectoryRepository(Protocol):
    """Employees and sites.

    ``changes`` dictionaries are keyed by ``Employee`` attribute, with ``site_id`` and
    ``manager_id`` in place of the references.
    """

    async def list_employees(
        self,
        *,
        terms: list[str],
        site_id: UUID | None,
        department: str | None,
        public_only: bool,
        include_inactive: bool,
        offset: int,
        limit: int,
    ) -> tuple[list[Employee], int]:
        """A page of employees ordered by name, and the total."""

    async def get_employee(self, employee_id: UUID) -> Employee | None:
        """One employee."""

    async def get_employee_by_user(self, user_id: UUID) -> Employee | None:
        """The employee of a platform user."""

    async def get_employee_by_oid(self, object_id: str) -> Employee | None:
        """The employee of an Entra object id."""

    async def synced_employees(self) -> list[Employee]:
        """Every employee synced from Entra, active or not."""

    async def birthdays(self, month: int | None) -> list[Employee]:
        """Public employees born in that month (or the whole year), by month and day."""

    async def audience_ids(
        self,
        *,
        site_ids: list[UUID] | None = None,
        departments: list[str] | None = None,
        employee_ids: list[UUID] | None = None,
    ) -> list[UUID]:
        """Active employees of an audience."""

    async def department_counts(self) -> list[tuple[str, int]]:
        """Departments of the active employees with their sizes."""

    async def get_schedule(self, employee_id: UUID) -> list[ScheduleSlot]:
        """The contact windows of an employee."""

    async def replace_schedule(self, employee_id: UUID, slots: list[ScheduleSlot]) -> None:
        """Replace every contact window of an employee."""

    async def new_hires(self, today: date, limit: int) -> list[Employee]:
        """Public employees by hire date, newest first, up to today."""

    async def team(self, employee: Employee, limit: int) -> list[Employee]:
        """Public employees of the same department, without the employee."""

    async def create_employee(self, fields: dict[str, Any]) -> Employee:
        """Insert an employee."""

    async def update_employee(self, employee_id: UUID, changes: dict[str, Any]) -> Employee | None:
        """Apply changes; ``None`` when the employee does not exist."""

    async def delete_employee(self, employee_id: UUID) -> bool:
        """Delete; ``False`` when it did not exist."""

    async def mark_unseen_inactive(self, seen_object_ids: Iterable[str]) -> list[UUID]:
        """Deactivate active synced employees Entra no longer lists.

        Returns:
            list[UUID]: The platform users of those employees.
        """

    async def recompute_is_manager(self) -> None:
        """Flag as manager every employee with an active direct report."""

    async def list_sites(self, include_inactive: bool) -> list[Site]:
        """Sites by name, with their count of public employees."""

    async def get_site(self, site_id: UUID) -> Site | None:
        """One site."""

    async def create_site(self, name: str, address: str | None, is_active: bool) -> Site:
        """Insert a site; ``ConflictError`` when the name is taken."""

    async def update_site(self, site_id: UUID, changes: dict[str, Any]) -> Site | None:
        """Apply changes; ``ConflictError`` when the new name is taken."""

    async def delete_site(self, site_id: UUID) -> bool:
        """Delete; ``ConflictError`` while employees use it."""


class SyncRunRepository(Protocol):
    """History of the directory syncs, with one running at most."""

    async def start(self, trigger: RunTrigger, triggered_by: str | None, now: datetime) -> SyncRun:
        """Insert a running sync; ``ConflictError`` when one is already running."""

    async def finish(
        self,
        run_id: UUID,
        status: RunStatus,
        counts: dict[str, int],
        now: datetime,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> SyncRun:
        """Close a sync."""

    async def latest(self) -> SyncRun | None:
        """The newest sync."""

    async def last_success(self) -> SyncRun | None:
        """The newest successful sync."""

    async def history(self, limit: int) -> list[SyncRun]:
        """Newest syncs first."""

    async def fail_running(self, error_code: str, error_message: str, now: datetime) -> int:
        """Close every running sync as failed; returns how many."""


class GraphDirectory(Protocol):
    """Read users, managers and photos from Microsoft Graph."""

    async def list_users(self) -> list[GraphUser]:
        """Every user of the tenant."""

    async def get_manager(self, object_id: str) -> str | None:
        """Object id of the user's manager."""

    async def get_photo_meta(self, object_id: str) -> GraphPhotoMeta | None:
        """Photo version, or ``None`` when the user has no photo."""

    async def get_photo(self, object_id: str, size: int) -> GraphPhoto | None:
        """The photo at that size, or ``None``."""


class UserProvisioning(Protocol):
    """Create and block platform users (implemented by identity)."""

    async def upsert_user(
        self,
        object_id: str,
        *,
        name: str,
        email: str | None,
        job_title: str | None,
        is_active: bool | None,
    ):
        """Create or refresh a user; returns a ``ProvisionedUser``."""

    async def deactivate_users(self, user_ids: Iterable[UUID]):
        """Block users; returns a ``DeactivationResult``."""


class PhotoStorage(Protocol):
    """Object storage for the photos."""

    async def put_object(self, key: str, body: bytes, content_type: str | None = None) -> None:
        """Write an object."""

    async def get_object(self, key: str) -> bytes:
        """Read an object."""

    async def delete_object(self, key: str) -> None:
        """Delete an object."""
