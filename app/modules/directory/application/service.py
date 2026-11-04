"""Use cases of the employee directory: reading it, editing it and asking for a sync."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.modules.directory.application.schedule import next_run_at
from app.modules.directory.domain.entities import (
    PENDING_CONTACT,
    PENDING_SITE,
    PHOTO_SIZES,
    SYNCED_FIELDS,
    Employee,
    RunStatus,
    RunTrigger,
    ScheduleSlot,
    Site,
    SyncRun,
    Visibility,
    clean_phone,
    validate_schedule,
)
from app.modules.directory.domain.ports import DirectoryRepository, PhotoStorage, SyncRunRepository
from app.modules.directory.domain.search import search_terms
from app.modules.identity.domain.principal import Principal

MAX_PAGE_SIZE = 100
MAX_HISTORY = 100
_EMPLOYEE_FIELDS = frozenset(
    {
        "full_name",
        "email",
        "position",
        "department",
        "site_id",
        "birth_date",
        "hire_date",
        "manager_id",
        "visibility",
        "is_manager",
        "is_active",
    }
)
_SITE_FIELDS = frozenset({"name", "address", "is_active"})


@dataclass(frozen=True)
class SyncStatus:
    """The background sync: on or off, when it runs next and the newest run."""

    enabled: bool
    scheduled_at: time
    timezone: str
    next_run_at: datetime | None
    running: bool
    latest: SyncRun | None


@dataclass(frozen=True)
class EmployeePhoto:
    """A photo ready to serve."""

    content: bytes
    content_type: str
    version: str


class DirectoryService:
    """Read and edit the directory; synced fields stay in Entra's hands."""

    def __init__(
        self,
        repository: DirectoryRepository,
        runs: SyncRunRepository,
        storage: PhotoStorage,
        sync_enabled: bool,
        sync_at: time,
        timezone: str,
        request_sync: Callable[[UUID], None],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Create the service.

        Args:
            repository (DirectoryRepository): Employees and sites.
            runs (SyncRunRepository): Sync history.
            storage (PhotoStorage): Object storage for the photos.
            sync_enabled (bool): Whether the background sync is on.
            sync_at (time): Local time of the daily sync.
            timezone (str): Timezone of ``sync_at`` and of "today".
            request_sync (Callable[[UUID], None]): Wakes the background sync for a run.
            clock (Callable[[], datetime] | None): Current instant, for tests.
        """

        self.repository = repository
        self.runs = runs
        self.storage = storage
        self.sync_enabled = sync_enabled
        self.sync_at = sync_at
        self.timezone = timezone
        self.zone = ZoneInfo(timezone)
        self.request_sync_now = request_sync
        self.clock = clock or (lambda: datetime.now(UTC))

    def today(self) -> date:
        """Today in the directory's timezone."""

        return self.clock().astimezone(self.zone).date()

    # ------------------------------------------------------------------ reading

    async def list_employees(
        self,
        q: str | None = None,
        site_id: UUID | None = None,
        department: str | None = None,
        page: int = 1,
        size: int = 24,
        *,
        include_hidden: bool = False,
        include_inactive: bool = False,
    ) -> tuple[list[Employee], int]:
        """A page of employees ordered by name.

        Args:
            q (str | None): Words that start words of the name, position or email.
            site_id (UUID | None): Only this site.
            department (str | None): Only this department (exact).
            page (int): Page number from 1.
            size (int): Page size, at most ``MAX_PAGE_SIZE``.
            include_hidden (bool): Administrators: also hidden employees.
            include_inactive (bool): Administrators: also inactive employees.

        Returns:
            tuple[list[Employee], int]: The page and the total.
        """

        _check_page(page, size)
        return await self.repository.list_employees(
            terms=search_terms(q),
            site_id=site_id,
            department=(department or "").strip() or None,
            public_only=not include_hidden,
            include_inactive=include_inactive,
            offset=(page - 1) * size,
            limit=size,
        )

    async def get_employee(self, employee_id: UUID, *, admin: bool = False) -> Employee:
        """One employee; others than administrators only see the public directory.

        Args:
            employee_id (UUID): The employee.
            admin (bool): Whether the caller is an administrator.

        Returns:
            Employee: The employee.

        Raises:
            NotFoundError: When it does not exist or is not public.
        """

        employee = await self.repository.get_employee(employee_id)
        if employee is None or not (admin or employee.is_public):
            raise NotFoundError("El empleado no existe")
        return employee

    async def my_employee(self, principal: Principal) -> Employee:
        """The employee of the caller.

        Args:
            principal (Principal): The caller.

        Returns:
            Employee: Their employee.

        Raises:
            NotFoundError: Until the directory sync has brought them in.
        """

        employee = await self.repository.get_employee_by_user(principal.user_id)
        if employee is None:
            raise NotFoundError(
                "Todavía no estás en el directorio: aparecerás tras la sincronización"
            )
        return employee

    async def birthdays(self, month: int | None = None) -> tuple[int | None, list[Employee]]:
        """Public employees with a birth date, by month and day.

        Args:
            month (int | None): 1 to 12; ``None`` is the whole year in one answer.

        Returns:
            tuple[int | None, list[Employee]]: The month asked and its employees.
        """

        if month is not None and not 1 <= month <= 12:
            raise InvalidInputError("El mes debe estar entre 1 y 12")
        return month, await self.repository.birthdays(month)

    # ------------------------------------------------------------- my profile

    async def onboarding(self, principal: Principal) -> tuple[Employee, list[str], list[Site]]:
        """What completar perfil still asks the caller, and the sites to choose from.

        Args:
            principal (Principal): The caller.

        Returns:
            tuple[Employee, list[str], list[Site]]: Their employee, the pending sections
                (``sede`` without a site, ``contacto`` without a mobile) and active sites.
        """

        employee = await self.my_employee(principal)
        return employee, _pending(employee), await self.repository.list_sites(False)

    async def save_onboarding(
        self, principal: Principal, fields: dict[str, Any]
    ) -> tuple[Employee, list[str], list[Site]]:
        """Save the caller's site and contact data.

        Args:
            principal (Principal): The caller.
            fields (dict[str, Any]): ``site_id``, ``mobile_phone``, ``landline_phone``,
                ``show_mobile_in_directory``; only those sent change.

        Returns:
            tuple[Employee, list[str], list[Site]]: As ``onboarding`` after saving.

        Raises:
            InvalidInputError: Nothing sent, an unknown or inactive site, a bad phone.
        """

        employee = await self.my_employee(principal)
        changes: dict[str, Any] = {}
        if "site_id" in fields:
            site = await self.repository.get_site(fields["site_id"]) if fields["site_id"] else None
            if site is None or not site.is_active:
                raise InvalidInputError("La sede no existe o no está activa")
            changes["site_id"] = site.id
        for name, label in (("mobile_phone", "celular"), ("landline_phone", "teléfono fijo")):
            if name in fields:
                changes[name] = clean_phone(fields[name], label)
        if "show_mobile_in_directory" in fields:
            changes["show_mobile_in_directory"] = bool(fields["show_mobile_in_directory"])
        if not changes:
            raise InvalidInputError("No hay datos para guardar")
        await self.repository.update_employee(employee.id, changes)
        return await self.onboarding(principal)

    async def my_schedule(self, principal: Principal) -> list[ScheduleSlot]:
        """The caller's contact windows.

        Args:
            principal (Principal): The caller.

        Returns:
            list[ScheduleSlot]: By weekday and start.
        """

        employee = await self.my_employee(principal)
        return validate_schedule(await self.repository.get_schedule(employee.id))

    async def set_my_schedule(
        self, principal: Principal, slots: list[ScheduleSlot]
    ) -> list[ScheduleSlot]:
        """Replace the caller's contact windows.

        Args:
            principal (Principal): The caller.
            slots (list[ScheduleSlot]): The new windows; an empty list clears them.

        Returns:
            list[ScheduleSlot]: The saved windows, ordered.

        Raises:
            InvalidInputError: Unknown day, a window that ends before it starts or that
                overlaps another of the same day.
        """

        employee = await self.my_employee(principal)
        ordered = validate_schedule(slots)
        await self.repository.replace_schedule(employee.id, ordered)
        return ordered

    async def schedule(self, employee_id: UUID, *, admin: bool = False) -> list[ScheduleSlot]:
        """The contact windows of an employee of the public directory.

        Args:
            employee_id (UUID): The employee.
            admin (bool): Whether the caller is an administrator.

        Returns:
            list[ScheduleSlot]: By weekday and start.
        """

        employee = await self.get_employee(employee_id, admin=admin)
        return validate_schedule(await self.repository.get_schedule(employee.id))

    async def new_hires(self, limit: int = 6) -> list[Employee]:
        """Newest public employees by hire date, up to today.

        Args:
            limit (int): 1 to 24.

        Returns:
            list[Employee]: Newest first.
        """

        if not 1 <= limit <= 24:
            raise InvalidInputError("El límite debe estar entre 1 y 24")
        return await self.repository.new_hires(self.today(), limit)

    async def team(self, employee_id: UUID, limit: int = 3) -> list[Employee]:
        """Public colleagues of the same department.

        Args:
            employee_id (UUID): The employee.
            limit (int): 1 to 24.

        Returns:
            list[Employee]: Colleagues by name.
        """

        if not 1 <= limit <= 24:
            raise InvalidInputError("El límite debe estar entre 1 y 24")
        employee = await self.get_employee(employee_id)
        if not employee.department:
            return []
        return await self.repository.team(employee, limit)

    async def photo(self, employee_id: UUID, size: int, *, admin: bool = False) -> EmployeePhoto:
        """The photo of an employee.

        Args:
            employee_id (UUID): The employee.
            size (int): 96 (thumbnail) or 432.
            admin (bool): Whether the caller is an administrator.

        Returns:
            EmployeePhoto: Bytes, type and version.

        Raises:
            NotFoundError: When the employee or its photo does not exist.
        """

        if size not in PHOTO_SIZES:
            raise InvalidInputError("El tamaño de la foto debe ser 96 o 432")
        employee = await self.get_employee(employee_id, admin=admin)
        key = employee.photo_thumb_key if size == 96 else employee.photo_key
        key = key or employee.photo_key or employee.photo_thumb_key
        if key is None or employee.photo_version is None:
            raise NotFoundError("El empleado no tiene foto")
        try:
            content = await self.storage.get_object(key)
        except Exception as exc:
            raise NotFoundError("El empleado no tiene foto") from exc
        return EmployeePhoto(
            content=content,
            content_type=employee.photo_content_type or "image/jpeg",
            version=employee.photo_version,
        )

    async def list_sites(self, include_inactive: bool = False) -> list[Site]:
        """Sites by name with their count of public employees.

        Args:
            include_inactive (bool): Also the inactive sites.

        Returns:
            list[Site]: The sites.
        """

        return await self.repository.list_sites(include_inactive)

    # ------------------------------------------------------------------ editing

    async def create_employee(self, fields: dict[str, Any]) -> Employee:
        """Add a person Entra does not have (a contractor, a shared line).

        Args:
            fields (dict[str, Any]): Employee attributes; ``full_name`` is required.

        Returns:
            Employee: The new employee.
        """

        changes = await self._clean(fields, None)
        if not changes.get("full_name"):
            raise InvalidInputError("El nombre es obligatorio")
        changes.setdefault("position", "")
        changes["auto_visible"] = True
        return await self.repository.create_employee(changes)

    async def update_employee(self, employee_id: UUID, fields: dict[str, Any]) -> Employee:
        """Change an employee; on a synced one, the fields Entra owns are ignored.

        Args:
            employee_id (UUID): The employee.
            fields (dict[str, Any]): Attributes to change.

        Returns:
            Employee: The employee as it is now.
        """

        current = await self.get_employee(employee_id, admin=True)
        if current.is_synced:
            fields = {name: value for name, value in fields.items() if name not in SYNCED_FIELDS}
        changes = await self._clean(fields, current)
        if not changes:
            return current
        updated = await self.repository.update_employee(employee_id, changes)
        if updated is None:
            raise NotFoundError("El empleado no existe")
        return updated

    async def delete_employee(self, employee_id: UUID) -> None:
        """Delete a manual employee; synced ones leave when Entra drops them.

        Args:
            employee_id (UUID): The employee.

        Raises:
            ConflictError: When the employee comes from Entra.
        """

        current = await self.get_employee(employee_id, admin=True)
        if current.is_synced:
            raise ConflictError(
                "El empleado viene de Entra: se desactiva cuando lo retiren de Entra. "
                "Para que no aparezca, ocúltalo"
            )
        await self.repository.delete_employee(employee_id)

    async def create_site(self, name: str, address: str | None, is_active: bool = True) -> Site:
        """Add a site.

        Args:
            name (str): Unique name.
            address (str | None): Street address.
            is_active (bool): Whether it is in use.

        Returns:
            Site: The new site.
        """

        return await self.repository.create_site(_name(name), _text(address), is_active)

    async def update_site(self, site_id: UUID, fields: dict[str, Any]) -> Site:
        """Change a site.

        Args:
            site_id (UUID): The site.
            fields (dict[str, Any]): ``name``, ``address`` or ``is_active``.

        Returns:
            Site: The site as it is now.
        """

        changes = {name: value for name, value in fields.items() if name in _SITE_FIELDS}
        if "name" in changes:
            changes["name"] = _name(changes["name"])
        if "address" in changes:
            changes["address"] = _text(changes["address"])
        if "is_active" in changes and changes["is_active"] is None:
            raise InvalidInputError("is_active no puede ser nulo")
        site = await self.repository.update_site(site_id, changes)
        if site is None:
            raise NotFoundError("La sede no existe")
        return site

    async def delete_site(self, site_id: UUID) -> None:
        """Delete a site nobody uses.

        Args:
            site_id (UUID): The site.

        Raises:
            ConflictError: While employees use it.
        """

        if not await self.repository.delete_site(site_id):
            raise NotFoundError("La sede no existe")

    # ------------------------------------------------------------------ sync

    async def request_sync(self, triggered_by: str | None) -> UUID:
        """Ask the background sync to run now.

        Args:
            triggered_by (str | None): The administrator.

        Returns:
            UUID: The run, already registered as running.

        Raises:
            ConflictError: When the background sync is off or a sync is running.
        """

        if not self.sync_enabled:
            raise ConflictError(
                "La sincronización del directorio está apagada (DIRECTORY_SYNC_ENABLED)"
            )
        run = await self.runs.start(RunTrigger.MANUAL, triggered_by, self.clock())
        self.request_sync_now(run.id)
        return run.id

    async def sync_status(self) -> SyncStatus:
        """The background sync and its newest run.

        Returns:
            SyncStatus: On or off, next run and newest run.
        """

        latest = await self.runs.latest()
        return SyncStatus(
            enabled=self.sync_enabled,
            scheduled_at=self.sync_at,
            timezone=self.timezone,
            next_run_at=(
                next_run_at(self.clock(), self.sync_at, self.zone) if self.sync_enabled else None
            ),
            running=latest is not None and latest.status is RunStatus.RUNNING,
            latest=latest,
        )

    async def sync_history(self, limit: int = 20) -> list[SyncRun]:
        """Newest syncs first.

        Args:
            limit (int): 1 to ``MAX_HISTORY``.

        Returns:
            list[SyncRun]: The syncs.
        """

        if not 1 <= limit <= MAX_HISTORY:
            raise InvalidInputError(f"El límite debe estar entre 1 y {MAX_HISTORY}")
        return await self.runs.history(limit)

    # ------------------------------------------------------------------ helpers

    async def _clean(self, fields: dict[str, Any], current: Employee | None) -> dict[str, Any]:
        changes = {name: value for name, value in fields.items() if name in _EMPLOYEE_FIELDS}
        if "full_name" in changes:
            changes["full_name"] = _name(changes["full_name"], "nombre", 200)
        if "email" in changes:
            changes["email"] = (_text(changes["email"]) or "").lower() or None
        for name in ("position", "department"):
            if name in changes:
                value = _text(changes[name])
                changes[name] = (value or "") if name == "position" else value
        if "visibility" in changes:
            try:
                changes["visibility"] = Visibility(changes["visibility"])
            except ValueError as exc:
                raise InvalidInputError("La visibilidad debe ser auto, shown o hidden") from exc
        for name in ("is_manager", "is_active"):
            if name in changes and changes[name] is None:
                raise InvalidInputError(f"{name} no puede ser nulo")
        if changes.get("site_id") is not None:
            if await self.repository.get_site(changes["site_id"]) is None:
                raise InvalidInputError("La sede no existe")
        if changes.get("manager_id") is not None:
            if current is not None and changes["manager_id"] == current.id:
                raise InvalidInputError("Un empleado no puede ser su propio jefe")
            if await self.repository.get_employee(changes["manager_id"]) is None:
                raise InvalidInputError("El jefe no existe")
        birth_date = changes.get("birth_date")
        if birth_date is not None and birth_date >= self.today():
            raise InvalidInputError("La fecha de nacimiento debe ser anterior a hoy")
        return changes


def _pending(employee: Employee) -> list[str]:
    pending = []
    if employee.site is None:
        pending.append(PENDING_SITE)
    if not employee.mobile_phone:
        pending.append(PENDING_CONTACT)
    return pending


def _check_page(page: int, size: int) -> None:
    if page < 1:
        raise InvalidInputError("La página empieza en 1")
    if not 1 <= size <= MAX_PAGE_SIZE:
        raise InvalidInputError(f"El tamaño de página debe estar entre 1 y {MAX_PAGE_SIZE}")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned or None


def _name(value: Any, field: str = "nombre", max_length: int = 150) -> str:
    cleaned = _text(value)
    if not cleaned:
        raise InvalidInputError(f"El {field} no puede estar vacío")
    if len(cleaned) > max_length:
        raise InvalidInputError(f"El {field} admite máximo {max_length} caracteres")
    return cleaned
