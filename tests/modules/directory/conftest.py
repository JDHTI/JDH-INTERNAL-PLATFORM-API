"""In-memory adapters for the directory tests."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.errors import ConflictError
from app.modules.directory.domain.entities import (
    Employee,
    EmployeeRef,
    GraphPhoto,
    GraphPhotoMeta,
    GraphUser,
    RunStatus,
    Site,
    SiteRef,
    SyncRun,
    Visibility,
)
from app.modules.directory.domain.search import build_search_text
from app.modules.identity.domain.entities import DeactivationResult, ProvisionedUser
from tests.modules.fakes import FakeStorage

NOW = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)


def graph_user(oid: str, name: str, **fields) -> GraphUser:
    email = fields.pop("mail", f"{oid}@jdh.com.co")
    return GraphUser(
        object_id=oid,
        display_name=name,
        mail=email,
        user_principal_name=fields.pop("upn", email),
        **fields,
    )


class FakeGraph:
    def __init__(self, users=()) -> None:
        self.users: list[GraphUser] = list(users)
        self.managers: dict[str, str] = {}
        self.photos: dict[str, str] = {}  # oid -> etag
        self.fail_users: Exception | None = None
        self.fail_manager: set[str] = set()
        self.downloads: list[tuple[str, int]] = []

    async def list_users(self):
        if self.fail_users:
            raise self.fail_users
        return list(self.users)

    async def get_manager(self, object_id):
        if object_id in self.fail_manager:
            raise RuntimeError("graph manager failed")
        return self.managers.get(object_id)

    async def get_photo_meta(self, object_id):
        etag = self.photos.get(object_id)
        return None if etag is None else GraphPhotoMeta(etag=etag)

    async def get_photo(self, object_id, size):
        self.downloads.append((object_id, size))
        return GraphPhoto(content=f"{object_id}-{size}".encode(), content_type="image/jpeg")


class FakeProvisioning:
    def __init__(self) -> None:
        self.users: dict[str, SimpleNamespace] = {}
        self.protected: set[str] = set()
        self.deactivated: list[uuid.UUID] = []

    async def upsert_user(self, object_id, *, name, email, job_title, is_active):
        user = self.users.get(object_id)
        created = user is None
        if created:
            user = SimpleNamespace(id=uuid.uuid4(), is_active=True)
            self.users[object_id] = user
        user.name, user.email, user.job_title = name, email, job_title
        protected = is_active is False and object_id in self.protected
        if is_active is not None and not protected:
            user.is_active = is_active
        user.last_change = is_active
        return ProvisionedUser(user.id, created, user.is_active, protected)

    async def deactivate_users(self, user_ids):
        ids = list(user_ids)
        protected_ids = {self.users[oid].id for oid in self.protected if oid in self.users}
        done = [i for i in ids if i not in protected_ids]
        self.deactivated.extend(done)
        return DeactivationResult(len(done), tuple(i for i in ids if i in protected_ids))


class FakeRepository:
    def __init__(self) -> None:
        self.employees: dict[uuid.UUID, Employee] = {}
        self.sites: dict[uuid.UUID, Site] = {}

    def add_site(self, name, address=None, is_active=True) -> Site:
        site = Site(uuid.uuid4(), name, address, is_active, NOW, NOW)
        self.sites[site.id] = site
        return site

    def _build(self, base: Employee, changes: dict) -> Employee:
        changes = dict(changes)
        if "site_id" in changes:
            site_id = changes.pop("site_id")
            site = self.sites.get(site_id) if site_id else None
            changes["site"] = SiteRef(site.id, site.name) if site else None
        if "manager_id" in changes:
            manager_id = changes.pop("manager_id")
            manager = self.employees.get(manager_id) if manager_id else None
            changes["manager"] = (
                EmployeeRef(manager.id, manager.full_name, manager.position) if manager else None
            )
        if "visibility" in changes:
            changes["visibility"] = Visibility(changes["visibility"])
        return replace(base, **changes, updated_at=NOW)

    def search_text(self, employee: Employee) -> str:
        return build_search_text(
            employee.full_name, employee.position, employee.job_title, employee.email
        )

    async def list_employees(
        self, *, terms, site_id, department, public_only, include_inactive, offset, limit
    ):
        rows = []
        for employee in self.employees.values():
            text = self.search_text(employee)
            if any(f" {term}" not in text for term in terms):
                continue
            if public_only and not employee.is_public:
                continue
            if not public_only and not include_inactive and not employee.is_active:
                continue
            if site_id and employee.site_id != site_id:
                continue
            if department and employee.department != department:
                continue
            rows.append(employee)
        rows.sort(key=lambda e: e.full_name)
        return rows[offset : offset + limit], len(rows)

    async def get_employee(self, employee_id):
        return self.employees.get(employee_id)

    async def get_employee_by_user(self, user_id):
        return next((e for e in self.employees.values() if e.user_id == user_id), None)

    async def get_employee_by_oid(self, object_id):
        return next((e for e in self.employees.values() if e.entra_object_id == object_id), None)

    async def synced_employees(self):
        return [e for e in self.employees.values() if e.entra_object_id]

    async def birthdays(self, month):
        rows = [
            e
            for e in self.employees.values()
            if e.is_public and e.birth_date and e.birth_date.month == month
        ]
        return sorted(rows, key=lambda e: (e.birth_date.day, e.full_name))

    async def new_hires(self, today, limit):
        rows = [
            e
            for e in self.employees.values()
            if e.is_public and e.hire_date and e.hire_date <= today
        ]
        return sorted(rows, key=lambda e: e.hire_date, reverse=True)[:limit]

    async def team(self, employee, limit):
        rows = [
            e
            for e in self.employees.values()
            if e.is_public and e.department == employee.department and e.id != employee.id
        ]
        return sorted(rows, key=lambda e: e.full_name)[:limit]

    async def create_employee(self, fields):
        employee = self._build(
            Employee(id=uuid.uuid4(), full_name=fields["full_name"], created_at=NOW), fields
        )
        self.employees[employee.id] = employee
        return employee

    async def update_employee(self, employee_id, changes):
        current = self.employees.get(employee_id)
        if current is None:
            return None
        self.employees[employee_id] = self._build(current, changes)
        return self.employees[employee_id]

    async def delete_employee(self, employee_id):
        return self.employees.pop(employee_id, None) is not None

    async def mark_unseen_inactive(self, seen_object_ids):
        seen = set(seen_object_ids)
        users = []
        for employee in list(self.employees.values()):
            oid = employee.entra_object_id
            if oid and oid not in seen and employee.is_active:
                self.employees[employee.id] = replace(
                    employee, is_active=False, account_enabled=False
                )
                users.append(employee.user_id)
        return users

    async def recompute_is_manager(self):
        managers = {e.manager_id for e in self.employees.values() if e.is_active and e.manager}
        for employee in list(self.employees.values()):
            if employee.id in managers and not employee.is_manager:
                self.employees[employee.id] = replace(employee, is_manager=True)

    async def list_sites(self, include_inactive):
        rows = [s for s in self.sites.values() if include_inactive or s.is_active]
        return [
            replace(
                s,
                employee_count=sum(
                    1 for e in self.employees.values() if e.site_id == s.id and e.is_public
                ),
            )
            for s in sorted(rows, key=lambda s: s.name)
        ]

    async def get_site(self, site_id):
        return self.sites.get(site_id)

    async def create_site(self, name, address, is_active):
        if any(s.name.lower() == name.lower() for s in self.sites.values()):
            raise ConflictError(f"Ya existe la sede {name}")
        return self.add_site(name, address, is_active)

    async def update_site(self, site_id, changes):
        site = self.sites.get(site_id)
        if site is None:
            return None
        self.sites[site_id] = replace(site, **changes)
        return self.sites[site_id]

    async def delete_site(self, site_id):
        if site_id not in self.sites:
            return False
        if any(e.site_id == site_id for e in self.employees.values()):
            raise ConflictError("La sede tiene empleados")
        self.sites.pop(site_id)
        return True


class FakeRuns:
    def __init__(self) -> None:
        self.runs: dict[uuid.UUID, SyncRun] = {}

    async def start(self, trigger, triggered_by, now):
        if any(run.status is RunStatus.RUNNING for run in self.runs.values()):
            raise ConflictError("Ya hay una sincronización del directorio en curso")
        run = SyncRun(uuid.uuid4(), trigger, RunStatus.RUNNING, now, triggered_by=triggered_by)
        self.runs[run.id] = run
        return run

    async def finish(self, run_id, status, counts, now, error_code=None, error_message=None):
        run = replace(
            self.runs[run_id],
            status=status,
            counts=counts,
            finished_at=now,
            error_code=error_code,
            error_message=error_message,
        )
        self.runs[run_id] = run
        return run

    async def history(self, limit):
        return sorted(self.runs.values(), key=lambda r: r.started_at, reverse=True)[:limit]

    async def latest(self):
        rows = await self.history(1)
        return rows[0] if rows else None

    async def last_success(self):
        rows = [r for r in await self.history(100) if r.status is RunStatus.SUCCESS]
        return rows[0] if rows else None

    async def fail_running(self, error_code, error_message, now):
        running = [r for r in self.runs.values() if r.status is RunStatus.RUNNING]
        for run in running:
            await self.finish(run.id, RunStatus.FAILED, run.counts, now, error_code, error_message)
        return len(running)


@pytest.fixture
def repository() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def runs() -> FakeRuns:
    return FakeRuns()


@pytest.fixture
def provisioning() -> FakeProvisioning:
    return FakeProvisioning()


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()
