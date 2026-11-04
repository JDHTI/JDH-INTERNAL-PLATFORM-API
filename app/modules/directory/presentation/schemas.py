"""HTTP models of the employee directory."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.directory.application.service import SyncStatus
from app.modules.directory.domain.entities import Employee, ScheduleSlot, Site, SyncRun

VisibilityValue = Literal["auto", "shown", "hidden"]


class SiteRefResponse(BaseModel):
    """A site as an employee shows it."""

    id: UUID
    name: str


class EmployeeRefResponse(BaseModel):
    """Another employee as a profile shows it."""

    id: UUID
    full_name: str
    position: str


class EmployeeSummary(BaseModel):
    """An employee in a list. ``photo_version`` goes in the photo URL as ``v``."""

    id: UUID
    full_name: str
    email: str | None
    position: str
    job_title: str | None
    department: str | None
    site: SiteRefResponse | None
    is_manager: bool
    has_photo: bool
    photo_version: str | None


class BirthdaySummary(EmployeeSummary):
    """An employee among the birthdays (the year of birth is not shown)."""

    birth_month: int
    birth_day: int


class NewHireSummary(EmployeeSummary):
    """An employee among the newest hires."""

    hire_date: date


class EmployeeDetail(EmployeeSummary):
    """The profile of an employee."""

    user_id: UUID | None
    hire_date: date | None
    birth_month: int | None
    birth_day: int | None
    manager: EmployeeRefResponse | None
    landline_phone: str | None
    # Only when the person chose to show it in the directory.
    mobile_phone: str | None


class AdminEmployee(EmployeeDetail):
    """An employee with everything an administrator edits or checks."""

    entra_object_id: str | None
    is_synced: bool
    is_active: bool
    visibility: VisibilityValue
    auto_visible: bool
    is_public: bool
    account_enabled: bool | None
    user_type: str | None
    office_location: str | None
    birth_date: date | None
    dwh_synced_at: datetime | None
    synced_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None


class SiteResponse(BaseModel):
    """A physical location of the company."""

    id: UUID
    name: str
    address: str | None
    is_active: bool
    employee_count: int
    created_at: datetime | None
    updated_at: datetime | None


class SyncRunResponse(BaseModel):
    """One directory sync."""

    id: UUID
    trigger: Literal["manual", "scheduled"]
    status: Literal["running", "success", "failed"]
    started_at: datetime
    finished_at: datetime | None
    counts: dict[str, int]
    error_code: str | None
    error_message: str | None
    triggered_by: str | None


class SyncStatusResponse(BaseModel):
    """The background sync and its newest run."""

    enabled: bool
    scheduled_at: str
    timezone: str
    next_run_at: datetime | None
    running: bool
    latest: SyncRunResponse | None


class SyncRequested(BaseModel):
    """A sync was registered and handed to the background loop."""

    run_id: UUID


class ItemsResponse[T](BaseModel):
    """A plain list."""

    items: list[T]


class PageResponse[T](BaseModel):
    """A page of a listing."""

    items: list[T]
    total: int
    page: int
    size: int


class BirthdaysResponse(BaseModel):
    """Employees with a birth date, by month and day; ``month`` is null for the whole year."""

    month: int | None
    items: list[BirthdaySummary]


Weekday = Literal["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"]


class ScheduleSlotModel(BaseModel):
    """One contact window: weekday and ``HH:MM`` range."""

    dia: Weekday
    inicio: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    fin: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class ScheduleModel(BaseModel):
    """The weekly contact schedule (the intranet's contract)."""

    franjas: list[ScheduleSlotModel] = Field(max_length=42)


class SiteOption(BaseModel):
    id: UUID
    nombre: str


class OnboardingResponse(BaseModel):
    """What completar perfil still asks, prefilled (the intranet's contract, camelCase)."""

    pendientes: list[Literal["sede", "contacto"]]
    siteId: UUID | None  # noqa: N815 - the intranet's field names
    mobilePhone: str  # noqa: N815
    landlinePhone: str  # noqa: N815
    showInDirectory: bool  # noqa: N815
    sedes: list[SiteOption]


class OnboardingRequest(BaseModel):
    """Only the fields sent change."""

    siteId: UUID | None = None  # noqa: N815
    mobilePhone: str | None = Field(None, max_length=30)  # noqa: N815
    landlinePhone: str | None = Field(None, max_length=30)  # noqa: N815
    showInDirectory: bool | None = None  # noqa: N815

    def as_changes(self) -> dict[str, Any]:
        """Sent fields by employee attribute."""

        names = {
            "siteId": "site_id",
            "mobilePhone": "mobile_phone",
            "landlinePhone": "landline_phone",
            "showInDirectory": "show_mobile_in_directory",
        }
        return {names[key]: value for key, value in self.model_dump(exclude_unset=True).items()}


class EmployeeChanges(BaseModel):
    """Fields to change; only those sent are touched.

    On an employee synced from Entra, ``full_name``, ``email``, ``department``,
    ``is_active``, ``hire_date`` and ``manager_id`` belong to Entra and are ignored.
    """

    full_name: str | None = Field(None, max_length=200)
    email: str | None = Field(None, max_length=320)
    position: str | None = Field(None, max_length=200)
    department: str | None = Field(None, max_length=200)
    site_id: UUID | None = None
    birth_date: date | None = None
    hire_date: date | None = None
    manager_id: UUID | None = None
    visibility: VisibilityValue | None = None
    is_manager: bool | None = None
    is_active: bool | None = None

    def as_changes(self) -> dict[str, Any]:
        """Only the fields the client sent."""

        return self.model_dump(exclude_unset=True)


class NewEmployee(EmployeeChanges):
    """A person Entra does not have."""

    full_name: str = Field(max_length=200)


class SiteChanges(BaseModel):
    """Fields of a site to change."""

    name: str | None = Field(None, max_length=150)
    address: str | None = Field(None, max_length=300)
    is_active: bool | None = None

    def as_changes(self) -> dict[str, Any]:
        """Only the fields the client sent."""

        return self.model_dump(exclude_unset=True)


class NewSite(BaseModel):
    """A physical location."""

    name: str = Field(max_length=150)
    address: str | None = Field(None, max_length=300)
    is_active: bool = True


def _site_ref(employee: Employee) -> dict[str, Any] | None:
    return {"id": employee.site.id, "name": employee.site.name} if employee.site else None


def summary(employee: Employee) -> dict[str, Any]:
    """An employee as a list shows it."""

    return {
        "id": employee.id,
        "full_name": employee.full_name,
        "email": employee.email,
        "position": employee.position,
        "job_title": employee.job_title,
        "department": employee.department,
        "site": _site_ref(employee),
        "is_manager": employee.is_manager,
        "has_photo": employee.photo_version is not None,
        "photo_version": employee.photo_version,
    }


def detail(employee: Employee) -> dict[str, Any]:
    """An employee as its profile shows it."""

    manager = employee.manager
    birth = employee.birth_date
    return {
        **summary(employee),
        "user_id": employee.user_id,
        "hire_date": employee.hire_date,
        "birth_month": birth.month if birth else None,
        "birth_day": birth.day if birth else None,
        "manager": (
            {"id": manager.id, "full_name": manager.full_name, "position": manager.position}
            if manager
            else None
        ),
        "landline_phone": employee.landline_phone,
        "mobile_phone": employee.mobile_phone if employee.show_mobile_in_directory else None,
    }


def schedule(slots: list[ScheduleSlot]) -> dict[str, Any]:
    """The contact schedule in the intranet's shape."""

    return {
        "franjas": [
            {"dia": s.day, "inicio": s.start.strftime("%H:%M"), "fin": s.end.strftime("%H:%M")}
            for s in slots
        ]
    }


def onboarding(employee: Employee, pending: list[str], sites: list[Site]) -> dict[str, Any]:
    """Completar perfil's prefill."""

    return {
        "pendientes": pending,
        "siteId": employee.site.id if employee.site else None,
        "mobilePhone": employee.mobile_phone or "",
        "landlinePhone": employee.landline_phone or "",
        "showInDirectory": employee.show_mobile_in_directory,
        "sedes": [{"id": s.id, "nombre": s.name} for s in sites],
    }


def admin_detail(employee: Employee) -> dict[str, Any]:
    """An employee with its administration fields."""

    return {
        **detail(employee),
        "entra_object_id": employee.entra_object_id,
        "is_synced": employee.is_synced,
        "is_active": employee.is_active,
        "visibility": employee.visibility.value,
        "auto_visible": employee.auto_visible,
        "is_public": employee.is_public,
        "account_enabled": employee.account_enabled,
        "user_type": employee.user_type,
        "office_location": employee.office_location,
        "birth_date": employee.birth_date,
        "dwh_synced_at": employee.dwh_synced_at,
        "synced_at": employee.synced_at,
        "created_at": employee.created_at,
        "updated_at": employee.updated_at,
    }


def site(value: Site) -> dict[str, Any]:
    """A site."""

    return {
        "id": value.id,
        "name": value.name,
        "address": value.address,
        "is_active": value.is_active,
        "employee_count": value.employee_count,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
    }


def sync_run(run: SyncRun | None) -> dict[str, Any] | None:
    """A sync run."""

    if run is None:
        return None
    return {
        "id": run.id,
        "trigger": run.trigger.value,
        "status": run.status.value,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "counts": run.counts,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "triggered_by": run.triggered_by,
    }


def sync_status(status: SyncStatus) -> dict[str, Any]:
    """The background sync."""

    return {
        "enabled": status.enabled,
        "scheduled_at": status.scheduled_at.strftime("%H:%M"),
        "timezone": status.timezone,
        "next_run_at": status.next_run_at,
        "running": status.running,
        "latest": sync_run(status.latest),
    }
