"""Employees of the company, their sites and the history of the Entra sync."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.core.errors import InvalidInputError


class Visibility(StrEnum):
    """Whether an employee appears in the directory everyone sees."""

    # The rule decides: an enabled member account with a corporate email.
    AUTO = "auto"
    SHOWN = "shown"
    HIDDEN = "hidden"


class RunStatus(StrEnum):
    """State of one directory sync."""

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class RunTrigger(StrEnum):
    """Who started a directory sync."""

    MANUAL = "manual"
    SCHEDULED = "scheduled"


# Graph fields the sync owns on a synced employee: an administrator cannot edit them.
SYNCED_FIELDS = frozenset(
    {"full_name", "email", "job_title", "department", "is_active", "hire_date", "manager_id"}
)
PHOTO_SIZES = (96, 432)


@dataclass(frozen=True)
class SiteRef:
    """A site as an employee shows it."""

    id: UUID
    name: str


@dataclass(frozen=True)
class EmployeeRef:
    """Another employee as a profile shows it (the manager)."""

    id: UUID
    full_name: str
    position: str


@dataclass
class Site:
    """A physical location of the company (an office, a warehouse)."""

    id: UUID
    name: str
    address: str | None
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Employees of the public directory at this site.
    employee_count: int = 0


@dataclass
class Employee:
    """A person of the directory: synced from Entra, or added by hand."""

    id: UUID
    full_name: str
    user_id: UUID | None = None
    entra_object_id: str | None = None
    email: str | None = None
    job_title: str | None = None
    position: str = ""
    department: str | None = None
    office_location: str | None = None
    site: SiteRef | None = None
    is_active: bool = True
    account_enabled: bool | None = None
    user_type: str | None = None
    auto_visible: bool = True
    visibility: Visibility = Visibility.AUTO
    birth_date: date | None = None
    hire_date: date | None = None
    manager: EmployeeRef | None = None
    is_manager: bool = False
    dwh_synced_at: datetime | None = None
    photo_key: str | None = None
    photo_thumb_key: str | None = None
    photo_etag: str | None = None
    photo_content_type: str | None = None
    photo_updated_at: datetime | None = None
    synced_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Contact data the person keeps (completar perfil, /perfil/contacto).
    mobile_phone: str | None = None
    landline_phone: str | None = None
    show_mobile_in_directory: bool = False

    @property
    def is_synced(self) -> bool:
        """Whether Entra owns this employee (it has an object id and a platform user)."""

        return self.entra_object_id is not None

    @property
    def site_id(self) -> UUID | None:
        """Identifier of the site, if any."""

        return self.site.id if self.site else None

    @property
    def manager_id(self) -> UUID | None:
        """Identifier of the manager, if any."""

        return self.manager.id if self.manager else None

    @property
    def is_public(self) -> bool:
        """Whether everyone signed in sees this employee."""

        return is_public(self.is_active, self.visibility, self.auto_visible)

    @property
    def photo_version(self) -> str | None:
        """Changes whenever the photo does; the UI puts it in the photo URL."""

        if self.photo_key is None or self.photo_updated_at is None:
            return None
        return str(int(self.photo_updated_at.timestamp() * 1000))


@dataclass(frozen=True)
class GraphUser:
    """One user as Microsoft Graph lists it."""

    object_id: str
    display_name: str | None
    mail: str | None
    user_principal_name: str | None
    job_title: str | None = None
    department: str | None = None
    office_location: str | None = None
    account_enabled: bool = True
    user_type: str | None = "Member"
    hire_date: date | None = None

    @property
    def email(self) -> str | None:
        """The mail, or the sign-in name when the account has no mailbox."""

        return (self.mail or self.user_principal_name or "").strip().lower() or None

    @property
    def name(self) -> str:
        """Display name with single spaces, falling back to the email."""

        return " ".join((self.display_name or "").split()) or self.email or self.object_id

    @property
    def is_member(self) -> bool:
        """Members are staff; guests (B2B) are not employees."""

        return (self.user_type or "Member").lower() == "member"


@dataclass(frozen=True)
class GraphPhotoMeta:
    """What Graph says about a photo without downloading it."""

    etag: str | None


@dataclass(frozen=True)
class GraphPhoto:
    """A downloaded photo."""

    content: bytes
    content_type: str


@dataclass
class SyncCounts:
    """What a directory sync did."""

    total: int = 0
    created: int = 0
    updated: int = 0
    deactivated: int = 0
    without_site: int = 0
    photos: int = 0
    errors: int = 0
    skipped_guests: int = 0
    last_admin_protected: int = 0

    def as_dict(self) -> dict[str, int]:
        """The counts as stored in the run."""

        return asdict(self)


@dataclass
class SyncRun:
    """One directory sync, running or finished."""

    id: UUID
    trigger: RunTrigger
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None = None
    counts: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    triggered_by: str | None = None


WEEKDAYS = ("LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO")
MAX_SLOTS = 42
# What completar perfil still asks for.
PENDING_SITE = "sede"
PENDING_CONTACT = "contacto"


@dataclass(frozen=True)
class ScheduleSlot:
    """One contact window of a person: a weekday and a time range."""

    day: str
    start: time
    end: time


def validate_schedule(slots: list[ScheduleSlot]) -> list[ScheduleSlot]:
    """Check a weekly contact schedule and return it ordered.

    Args:
        slots (list[ScheduleSlot]): The windows.

    Returns:
        list[ScheduleSlot]: By weekday, then start time.

    Raises:
        InvalidInputError: Unknown day, a window that ends before it starts, windows that
            overlap on the same day, or too many windows.
    """

    if len(slots) > MAX_SLOTS:
        raise InvalidInputError(f"El horario admite máximo {MAX_SLOTS} franjas")
    for slot in slots:
        if slot.day not in WEEKDAYS:
            raise InvalidInputError(f"Día no válido: {slot.day}")
        if slot.end <= slot.start:
            raise InvalidInputError("Cada franja debe terminar después de empezar")
    ordered = sorted(slots, key=lambda s: (WEEKDAYS.index(s.day), s.start))
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if previous.day == current.day and current.start < previous.end:
            raise InvalidInputError(f"Las franjas del {current.day.lower()} se cruzan")
    return ordered


def clean_phone(value: str | None, field: str) -> str | None:
    """A phone number as typed, or ``None`` when blank.

    Args:
        value (str | None): What the person typed.
        field (str): Field name for the error.

    Returns:
        str | None: Digits, spaces, ``+``, ``-``, ``(`` and ``)`` kept; at most 30.

    Raises:
        InvalidInputError: When it has other characters, or too few digits.
    """

    text = " ".join((value or "").split())
    if not text:
        return None
    if len(text) > 30 or any(char not in "0123456789 +-()" for char in text):
        raise InvalidInputError(f"El {field} solo admite números, espacios, +, - y paréntesis")
    if sum(char.isdigit() for char in text) < 7:
        raise InvalidInputError(f"El {field} debe tener al menos 7 dígitos")
    return text


def is_public(is_active: bool, visibility: Visibility | str, auto_visible: bool) -> bool:
    """The rule of the public directory.

    Args:
        is_active (bool): The employee is active.
        visibility (Visibility | str): The administrator's choice.
        auto_visible (bool): What the automatic rule says.

    Returns:
        bool: Whether everyone signed in sees the employee.
    """

    choice = Visibility(visibility)
    return is_active and (
        choice is Visibility.SHOWN or (choice is Visibility.AUTO and auto_visible)
    )


def auto_visible(user: GraphUser, corporate_domains: list[str]) -> bool:
    """Whether a synced account is shown by default: enabled, member, corporate email.

    Args:
        user (GraphUser): The account as Graph lists it.
        corporate_domains (list[str]): Mail domains of the company, lowercase.

    Returns:
        bool: ``True`` when the account looks like a person of the company.
    """

    email = user.mail.strip().lower() if user.mail else ""
    domain = email.rpartition("@")[2]
    return user.account_enabled and user.is_member and bool(email) and domain in corporate_domains
