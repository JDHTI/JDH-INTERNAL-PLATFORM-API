"""PostgreSQL repositories of the employee directory."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, exists, extract, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased, selectinload

from app.core.errors import ConflictError
from app.infrastructure.database import Database
from app.modules.directory.domain.entities import (
    Employee,
    EmployeeRef,
    RunStatus,
    RunTrigger,
    ScheduleSlot,
    Site,
    SiteRef,
    SyncRun,
    Visibility,
)
from app.modules.directory.domain.search import build_search_text
from app.modules.directory.infrastructure.models import (
    DirectoryContactScheduleModel,
    DirectoryEmployeeModel,
    DirectorySiteModel,
    DirectorySyncRunModel,
)

_E = DirectoryEmployeeModel
_LOAD = (selectinload(_E.site), selectinload(_E.manager))
# Employee attributes stored as plain columns of the same name.
_COLUMNS = frozenset(
    {
        "user_id",
        "entra_object_id",
        "full_name",
        "email",
        "job_title",
        "position",
        "department",
        "office_location",
        "site_id",
        "is_active",
        "account_enabled",
        "user_type",
        "auto_visible",
        "visibility",
        "birth_date",
        "hire_date",
        "manager_id",
        "is_manager",
        "dwh_synced_at",
        "photo_key",
        "photo_thumb_key",
        "photo_etag",
        "photo_content_type",
        "photo_updated_at",
        "synced_at",
        "mobile_phone",
        "landline_phone",
        "show_mobile_in_directory",
    }
)


def _public():
    """The public directory: active, and shown by the administrator or by the rule."""

    return and_(
        _E.is_active.is_(True),
        or_(
            _E.visibility == Visibility.SHOWN.value,
            and_(_E.visibility == Visibility.AUTO.value, _E.auto_visible.is_(True)),
        ),
    )


def _employee(model: DirectoryEmployeeModel) -> Employee:
    site = model.site
    manager = model.manager
    return Employee(
        id=model.id,
        full_name=model.full_name,
        user_id=model.user_id,
        entra_object_id=model.entra_object_id,
        email=model.email,
        job_title=model.job_title,
        position=model.position,
        department=model.department,
        office_location=model.office_location,
        site=SiteRef(site.id, site.name) if site else None,
        is_active=model.is_active,
        account_enabled=model.account_enabled,
        user_type=model.user_type,
        auto_visible=model.auto_visible,
        visibility=Visibility(model.visibility),
        birth_date=model.birth_date,
        hire_date=model.hire_date,
        manager=EmployeeRef(manager.id, manager.full_name, manager.position) if manager else None,
        is_manager=model.is_manager,
        dwh_synced_at=model.dwh_synced_at,
        photo_key=model.photo_key,
        photo_thumb_key=model.photo_thumb_key,
        photo_etag=model.photo_etag,
        photo_content_type=model.photo_content_type,
        photo_updated_at=model.photo_updated_at,
        synced_at=model.synced_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
        mobile_phone=model.mobile_phone,
        landline_phone=model.landline_phone,
        show_mobile_in_directory=model.show_mobile_in_directory,
    )


def _site(model: DirectorySiteModel, employee_count: int = 0) -> Site:
    return Site(
        id=model.id,
        name=model.name,
        address=model.address,
        is_active=model.is_active,
        created_at=model.created_at,
        updated_at=model.updated_at,
        employee_count=employee_count,
    )


def _run(model: DirectorySyncRunModel) -> SyncRun:
    return SyncRun(
        id=model.id,
        trigger=RunTrigger(model.trigger),
        status=RunStatus(model.status),
        started_at=model.started_at,
        finished_at=model.finished_at,
        counts=dict(model.counts or {}),
        error_code=model.error_code,
        error_message=model.error_message,
        triggered_by=model.triggered_by,
    )


def _apply(model: DirectoryEmployeeModel, changes: dict[str, Any]) -> None:
    for name, value in changes.items():
        if name not in _COLUMNS:
            raise ValueError(f"Unknown employee field: {name}")
        setattr(model, name, value.value if isinstance(value, Visibility) else value)
    model.search_text = build_search_text(
        model.full_name, model.position, model.job_title, model.email
    )


class SqlAlchemyDirectoryRepository:
    """Employees and sites; each call opens and closes its own transaction."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    # ---------------------------------------------------------------- employees

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
        """A page of employees ordered by name, and the total.

        Args:
            terms (list[str]): Normalized words; each must start a word of the employee.
            site_id (UUID | None): Only this site.
            department (str | None): Only this department.
            public_only (bool): Only the public directory.
            include_inactive (bool): Also inactive employees (when not public only).
            offset (int): Rows to skip.
            limit (int): Rows to return.

        Returns:
            tuple[list[Employee], int]: The page and the total.
        """

        conditions = [_E.search_text.like(f"% {term}%") for term in terms]
        if public_only:
            conditions.append(_public())
        elif not include_inactive:
            conditions.append(_E.is_active.is_(True))
        if site_id is not None:
            conditions.append(_E.site_id == site_id)
        if department:
            conditions.append(_E.department == department)
        async with self.database.session_factory() as session:
            total = await session.scalar(select(func.count()).select_from(_E).where(*conditions))
            rows = await session.scalars(
                select(_E)
                .where(*conditions)
                .options(*_LOAD)
                .order_by(_E.full_name, _E.id)
                .offset(offset)
                .limit(limit)
            )
            return [_employee(row) for row in rows], int(total or 0)

    async def get_employee(self, employee_id: UUID) -> Employee | None:
        """One employee.

        Args:
            employee_id (UUID): The employee.

        Returns:
            Employee | None: The employee, if it exists.
        """

        return await self._one(_E.id == employee_id)

    async def get_employee_by_user(self, user_id: UUID) -> Employee | None:
        """The employee of a platform user.

        Args:
            user_id (UUID): Platform user.

        Returns:
            Employee | None: Their employee, if synced.
        """

        return await self._one(_E.user_id == user_id)

    async def get_employee_by_oid(self, object_id: str) -> Employee | None:
        """The employee of an Entra object id.

        Args:
            object_id (str): Entra object id.

        Returns:
            Employee | None: The employee, if synced.
        """

        return await self._one(_E.entra_object_id == object_id)

    async def synced_employees(self) -> list[Employee]:
        """Every employee synced from Entra, active or not.

        Returns:
            list[Employee]: The synced employees.
        """

        async with self.database.session_factory() as session:
            rows = await session.scalars(
                select(_E).where(_E.entra_object_id.is_not(None)).options(*_LOAD)
            )
            return [_employee(row) for row in rows]

    async def birthdays(self, month: int | None) -> list[Employee]:
        """Public employees with a birth date, by month, day and name.

        Args:
            month (int | None): 1 to 12, or ``None`` for the whole year.

        Returns:
            list[Employee]: The employees.
        """

        query = select(_E).where(_public(), _E.birth_date.is_not(None))
        if month is not None:
            query = query.where(extract("month", _E.birth_date) == month)
        async with self.database.session_factory() as session:
            rows = await session.scalars(
                query.options(*_LOAD).order_by(
                    extract("month", _E.birth_date), extract("day", _E.birth_date), _E.full_name
                )
            )
            return [_employee(row) for row in rows]

    async def get_schedule(self, employee_id: UUID) -> list[ScheduleSlot]:
        """The contact windows of an employee.

        Args:
            employee_id (UUID): The employee.

        Returns:
            list[ScheduleSlot]: The windows, unordered.
        """

        async with self.database.session_factory() as session:
            rows = await session.scalars(
                select(DirectoryContactScheduleModel).where(
                    DirectoryContactScheduleModel.employee_id == employee_id
                )
            )
            return [ScheduleSlot(row.day, row.start_time, row.end_time) for row in rows]

    async def replace_schedule(self, employee_id: UUID, slots: list[ScheduleSlot]) -> None:
        """Replace every contact window of an employee in one transaction.

        Args:
            employee_id (UUID): The employee.
            slots (list[ScheduleSlot]): The new windows, already validated.
        """

        async with self.database.session_factory() as session:
            await session.execute(
                delete(DirectoryContactScheduleModel).where(
                    DirectoryContactScheduleModel.employee_id == employee_id
                )
            )
            session.add_all(
                DirectoryContactScheduleModel(
                    employee_id=employee_id, day=slot.day, start_time=slot.start, end_time=slot.end
                )
                for slot in slots
            )
            await session.commit()

    async def new_hires(self, today: date, limit: int) -> list[Employee]:
        """Public employees by hire date, newest first, up to today.

        Args:
            today (date): Hires after this date are left out.
            limit (int): Rows to return.

        Returns:
            list[Employee]: The employees.
        """

        async with self.database.session_factory() as session:
            rows = await session.scalars(
                select(_E)
                .where(_public(), _E.hire_date.is_not(None), _E.hire_date <= today)
                .options(*_LOAD)
                .order_by(_E.hire_date.desc(), _E.full_name)
                .limit(limit)
            )
            return [_employee(row) for row in rows]

    async def team(self, employee: Employee, limit: int) -> list[Employee]:
        """Public employees of the same department, without the employee.

        Args:
            employee (Employee): The employee.
            limit (int): Rows to return.

        Returns:
            list[Employee]: Colleagues by name.
        """

        async with self.database.session_factory() as session:
            rows = await session.scalars(
                select(_E)
                .where(_public(), _E.department == employee.department, _E.id != employee.id)
                .options(*_LOAD)
                .order_by(_E.full_name, _E.id)
                .limit(limit)
            )
            return [_employee(row) for row in rows]

    async def create_employee(self, fields: dict[str, Any]) -> Employee:
        """Insert an employee.

        Args:
            fields (dict[str, Any]): Employee attributes (``site_id``, ``manager_id``).

        Returns:
            Employee: The new employee.
        """

        model = DirectoryEmployeeModel(id=uuid.uuid4())
        _apply(model, {"position": "", **fields})
        async with self.database.session_factory() as session:
            session.add(model)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("El empleado ya existe en el directorio") from exc
        created = await self.get_employee(model.id)
        assert created is not None
        return created

    async def update_employee(self, employee_id: UUID, changes: dict[str, Any]) -> Employee | None:
        """Apply changes.

        Args:
            employee_id (UUID): The employee.
            changes (dict[str, Any]): Attributes to set.

        Returns:
            Employee | None: The employee as it is now, or ``None`` when it does not exist.
        """

        async with self.database.session_factory() as session:
            model = await session.get(_E, employee_id)
            if model is None:
                return None
            _apply(model, changes)
            await session.commit()
        return await self.get_employee(employee_id)

    async def delete_employee(self, employee_id: UUID) -> bool:
        """Delete an employee; its reports lose their manager.

        Args:
            employee_id (UUID): The employee.

        Returns:
            bool: ``False`` when it did not exist.
        """

        async with self.database.session_factory() as session:
            model = await session.get(_E, employee_id)
            if model is None:
                return False
            await session.delete(model)
            await session.commit()
            return True

    async def mark_unseen_inactive(self, seen_object_ids: Iterable[str]) -> list[UUID]:
        """Deactivate the active synced employees Entra no longer lists.

        ``account_enabled`` goes to false too, so a return to Entra unblocks the user.

        Args:
            seen_object_ids (Iterable[str]): Object ids Graph listed in this sync.

        Returns:
            list[UUID]: The platform users of the deactivated employees.
        """

        seen = list(seen_object_ids)
        condition = and_(_E.entra_object_id.is_not(None), _E.is_active.is_(True))
        if seen:
            condition = and_(condition, _E.entra_object_id.not_in(seen))
        async with self.database.session_factory() as session:
            result = await session.execute(
                update(_E)
                .where(condition)
                .values(is_active=False, account_enabled=False)
                .returning(_E.user_id)
            )
            user_ids = [row[0] for row in result if row[0] is not None]
            await session.commit()
            return user_ids

    async def recompute_is_manager(self) -> None:
        """Flag as manager every employee with an active direct report."""

        report = aliased(_E)
        has_reports = exists().where(report.manager_id == _E.id, report.is_active.is_(True))
        async with self.database.session_factory() as session:
            await session.execute(
                update(_E).where(_E.is_manager.is_(False), has_reports).values(is_manager=True)
            )
            await session.commit()

    async def _one(self, condition) -> Employee | None:
        async with self.database.session_factory() as session:
            model = await session.scalar(select(_E).where(condition).options(*_LOAD))
            return _employee(model) if model else None

    # -------------------------------------------------------------------- sites

    async def list_sites(self, include_inactive: bool) -> list[Site]:
        """Sites by name, with their count of public employees.

        Args:
            include_inactive (bool): Also the inactive sites.

        Returns:
            list[Site]: The sites.
        """

        count = (
            select(func.count())
            .select_from(_E)
            .where(_E.site_id == DirectorySiteModel.id, _public())
            .scalar_subquery()
        )
        query = select(DirectorySiteModel, count).order_by(DirectorySiteModel.name)
        if not include_inactive:
            query = query.where(DirectorySiteModel.is_active.is_(True))
        async with self.database.session_factory() as session:
            rows = await session.execute(query)
            return [_site(model, int(total or 0)) for model, total in rows]

    async def get_site(self, site_id: UUID) -> Site | None:
        """One site.

        Args:
            site_id (UUID): The site.

        Returns:
            Site | None: The site, if it exists.
        """

        async with self.database.session_factory() as session:
            model = await session.get(DirectorySiteModel, site_id)
            return _site(model) if model else None

    async def create_site(self, name: str, address: str | None, is_active: bool) -> Site:
        """Insert a site.

        Args:
            name (str): Unique name.
            address (str | None): Street address.
            is_active (bool): Whether it is in use.

        Returns:
            Site: The new site.

        Raises:
            ConflictError: When the name is taken.
        """

        model = DirectorySiteModel(id=uuid.uuid4(), name=name, address=address, is_active=is_active)
        async with self.database.session_factory() as session:
            session.add(model)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError(f"Ya existe la sede {name}") from exc
            await session.refresh(model)
            return _site(model)

    async def update_site(self, site_id: UUID, changes: dict[str, Any]) -> Site | None:
        """Apply changes to a site.

        Args:
            site_id (UUID): The site.
            changes (dict[str, Any]): ``name``, ``address`` or ``is_active``.

        Returns:
            Site | None: The site as it is now, or ``None`` when it does not exist.

        Raises:
            ConflictError: When the new name is taken.
        """

        async with self.database.session_factory() as session:
            model = await session.get(DirectorySiteModel, site_id)
            if model is None:
                return None
            for name, value in changes.items():
                setattr(model, name, value)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("Ya existe una sede con ese nombre") from exc
            await session.refresh(model)
            return _site(model)

    async def delete_site(self, site_id: UUID) -> bool:
        """Delete a site.

        Args:
            site_id (UUID): The site.

        Returns:
            bool: ``False`` when it did not exist.

        Raises:
            ConflictError: While employees use it.
        """

        async with self.database.session_factory() as session:
            model = await session.get(DirectorySiteModel, site_id)
            if model is None:
                return False
            await session.delete(model)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError(
                    "La sede tiene empleados: muévelos o desactiva la sede"
                ) from exc
            return True


class SqlAlchemySyncRunRepository:
    """History of the directory syncs."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def start(self, trigger: RunTrigger, triggered_by: str | None, now: datetime) -> SyncRun:
        """Insert a running sync.

        Args:
            trigger (RunTrigger): Manual or scheduled.
            triggered_by (str | None): Who asked for it.
            now (datetime): Start instant.

        Returns:
            SyncRun: The running sync.

        Raises:
            ConflictError: When another sync is running (partial unique index).
        """

        model = DirectorySyncRunModel(
            id=uuid.uuid4(),
            trigger=trigger.value,
            status=RunStatus.RUNNING.value,
            started_at=now,
            counts={},
            triggered_by=triggered_by,
        )
        async with self.database.session_factory() as session:
            session.add(model)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("Ya hay una sincronización del directorio en curso") from exc
            return _run(model)

    async def finish(
        self,
        run_id: UUID,
        status: RunStatus,
        counts: dict[str, int],
        now: datetime,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> SyncRun:
        """Close a sync.

        Args:
            run_id (UUID): The sync.
            status (RunStatus): Success or failed.
            counts (dict[str, int]): What it did.
            now (datetime): End instant.
            error_code (str | None): Error class when it failed.
            error_message (str | None): Error text when it failed.

        Returns:
            SyncRun: The closed sync.
        """

        async with self.database.session_factory() as session:
            model = await session.get(DirectorySyncRunModel, run_id)
            if model is None:
                raise ValueError(f"Unknown directory sync run {run_id}")
            model.status = status.value
            model.counts = counts
            model.finished_at = now
            model.error_code = error_code
            model.error_message = error_message
            await session.commit()
            return _run(model)

    async def latest(self) -> SyncRun | None:
        """The newest sync.

        Returns:
            SyncRun | None: The sync, if any.
        """

        runs = await self.history(1)
        return runs[0] if runs else None

    async def last_success(self) -> SyncRun | None:
        """The newest successful sync.

        Returns:
            SyncRun | None: The sync, if any.
        """

        async with self.database.session_factory() as session:
            model = await session.scalar(
                select(DirectorySyncRunModel)
                .where(DirectorySyncRunModel.status == RunStatus.SUCCESS.value)
                .order_by(DirectorySyncRunModel.started_at.desc())
                .limit(1)
            )
            return _run(model) if model else None

    async def history(self, limit: int) -> list[SyncRun]:
        """Newest syncs first.

        Args:
            limit (int): Rows to return.

        Returns:
            list[SyncRun]: The syncs.
        """

        async with self.database.session_factory() as session:
            rows = await session.scalars(
                select(DirectorySyncRunModel)
                .order_by(DirectorySyncRunModel.started_at.desc())
                .limit(limit)
            )
            return [_run(row) for row in rows]

    async def fail_running(self, error_code: str, error_message: str, now: datetime) -> int:
        """Close every running sync as failed.

        Args:
            error_code (str): Stored error class.
            error_message (str): Stored error text.
            now (datetime): End instant.

        Returns:
            int: How many syncs were closed.
        """

        async with self.database.session_factory() as session:
            result = await session.execute(
                update(DirectorySyncRunModel)
                .where(DirectorySyncRunModel.status == RunStatus.RUNNING.value)
                .values(
                    status=RunStatus.FAILED.value,
                    finished_at=now,
                    error_code=error_code,
                    error_message=error_message,
                )
            )
            await session.commit()
            return int(result.rowcount or 0)
