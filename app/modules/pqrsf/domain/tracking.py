"""Follow-up of PQRSF cases: statuses, catalogs, items, timeline and SLA rules.

A request (``pqrsf_requests``) is the immutable record of what a customer
submitted. A case is the internal follow-up of that request, or of one raised
by a collaborator (the P.13.F.01 spreadsheet flow): it carries the consecutive
code, the responsible person, the status and the requested documents.
"""

from __future__ import annotations

import re
from collections.abc import Container
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from uuid import UUID

from app.core.errors import InvalidInputError
from app.modules.pqrsf.domain.entities import RequestType
from app.modules.sites.domain.entities import Site

CASE_ORIGINS = frozenset({"public_form", "internal", "import"})
_CODE_PATTERN = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
_PREFIX_PATTERN = re.compile(r"^[A-Z]{1,5}$")

# Seeded status codes the automations rely on. Statuses stay configurable:
# when one of these is missing or inactive the automation is skipped.
READ_STATUS_CODE = "leida"
IN_PROGRESS_STATUS_CODE = "en_curso"


class EventType:
    """Kinds of entries in a case timeline."""

    CREATED = "created"
    UPDATED = "updated"
    ASSIGNED = "assigned"
    STATUS_CHANGED = "status_changed"
    READ = "read"
    COMMENT = "comment"
    ITEM_ADDED = "item_added"
    ITEM_UPDATED = "item_updated"
    ITEM_REMOVED = "item_removed"


@dataclass
class CaseStatus:
    """A configurable follow-up status (recibida, leída, en curso, finalizada)."""

    id: UUID
    code: str
    name: str
    color: str | None
    sort_order: int
    is_initial: bool
    is_terminal: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass
class Dependency:
    """An internal scope responsible for the case (Comercial, Distribución)."""

    id: UUID
    code: str
    name: str
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass
class DocumentType:
    """A document a case can request, with its agreed response time."""

    id: UUID
    name: str
    sla_days: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass
class CaseItem:
    """One requested document (a row of the spreadsheet)."""

    id: UUID
    case_id: UUID
    document_type: DocumentType | None
    description: str | None
    quantity: int | None
    sla_days: int | None
    due_on: date | None
    responded_on: date | None
    response_days: int | None
    delay_cause: str | None
    delay_detail: str | None
    sort_order: int
    created_at: datetime
    updated_at: datetime

    @property
    def difference_days(self) -> int | None:
        """Business days over (positive) or under (negative) the agreed time."""

        if self.response_days is None or self.sla_days is None:
            return None
        return self.response_days - self.sla_days

    @property
    def is_late(self) -> bool:
        """Whether the answer took longer than the agreed time."""

        difference = self.difference_days
        return difference is not None and difference > 0


@dataclass
class CaseEvent:
    """An immutable entry of the case timeline."""

    id: UUID
    case_id: UUID
    event_type: str
    actor_user_id: UUID | None
    from_status_id: UUID | None
    to_status_id: UUID | None
    note: str | None
    data: dict
    created_at: datetime


@dataclass
class TrackingCase:
    """The follow-up of one PQRSF, identified by a consecutive such as P-2026-00012."""

    id: UUID
    code: str
    number: int
    year: int
    request_type: RequestType
    status: CaseStatus
    origin: str
    subject: str | None
    description: str | None
    received_on: date
    due_on: date | None
    site: Site | None
    dependency: Dependency | None
    assignee_user_id: UUID | None
    customer_name: str | None
    customer_nit: str | None
    supplier_name: str | None
    sales_executive_name: str | None
    requester_name: str | None
    requester_email: str | None
    request_id: UUID | None
    created_by_user_id: UUID | None
    first_read_at: datetime | None
    closed_at: datetime | None
    legacy: dict | None
    created_at: datetime
    updated_at: datetime
    items: list[CaseItem] = field(default_factory=list)

    @property
    def is_closed(self) -> bool:
        """Whether the case sits in a terminal status."""

        return self.status.is_terminal

    def is_overdue(self, today: date) -> bool:
        """Whether an open case is past its due date."""

        return not self.is_closed and self.due_on is not None and self.due_on < today


@dataclass(frozen=True)
class CaseFilters:
    """Filters for the case inbox. ``visible_to`` limits to a collaborator's cases."""

    status_ids: tuple[UUID, ...] = ()
    request_type_id: UUID | None = None
    assignee_user_id: UUID | None = None
    unassigned: bool = False
    dependency_id: UUID | None = None
    site_id: UUID | None = None
    open_only: bool = False
    overdue: bool = False
    due_today: bool = False
    received_from: date | None = None
    received_to: date | None = None
    query: str | None = None
    visible_to: UUID | None = None
    sort: str = "-received_on"


CASE_SORTS = frozenset(
    {
        "received_on",
        "-received_on",
        "due_on",
        "-due_on",
        "code",
        "-code",
        "updated_at",
        "-updated_at",
    }
)


@dataclass(frozen=True)
class CasePage:
    """One page of cases."""

    items: list[TrackingCase]
    total: int
    page: int
    size: int


@dataclass(frozen=True)
class CountBucket:
    """A labelled count for dashboards."""

    id: UUID
    code: str
    name: str
    count: int
    color: str | None = None


@dataclass(frozen=True)
class CaseSummary:
    """Inbox indicators for the visible cases."""

    total: int
    open: int
    overdue: int
    due_today: int
    unassigned: int
    by_status: list[CountBucket]
    by_type: list[CountBucket]


@dataclass(frozen=True)
class ItemInput:
    """A document requested when creating or extending a case."""

    document_type_id: UUID | None
    description: str | None = None
    quantity: int | None = None


@dataclass(frozen=True)
class CaseInput:
    """Fields a collaborator provides when raising a case internally."""

    request_type_id: UUID
    received_on: date | None = None
    subject: str | None = None
    description: str | None = None
    site_id: UUID | None = None
    dependency_id: UUID | None = None
    customer_name: str | None = None
    customer_nit: str | None = None
    supplier_name: str | None = None
    sales_executive_name: str | None = None
    requester_name: str | None = None
    requester_email: str | None = None
    assignee_user_id: UUID | None = None
    items: tuple[ItemInput, ...] = ()


def case_code(prefix: str, year: int, number: int) -> str:
    """Format the consecutive: prefix, year and a zero-padded counter.

    The counter restarts every year per request type, as the paper format did.

    Args:
        prefix (str): Request type prefix, e.g. ``P``.
        year (int): Year of reception.
        number (int): Counter within the type and year.

    Returns:
        str: Code such as ``P-2026-00012``.
    """

    return f"{prefix}-{year}-{number:05d}"


def is_business_day(day: date, holidays: Container[date]) -> bool:
    """Monday to Friday and not a holiday."""

    return day.weekday() < 5 and day not in holidays


def business_days_between(start: date, end: date, holidays: Container[date]) -> int:
    """Count business days from ``start`` to ``end``, both included.

    Mirrors the spreadsheet's NETWORKDAYS: an answer on the day of reception
    counts as one day. Returns a negative count when ``end`` precedes ``start``.

    Args:
        start (date): Reception date.
        end (date): Response date.
        holidays (Container[date]): Non-working dates.

    Returns:
        int: Business days in the inclusive range.
    """

    if end < start:
        return -business_days_between(end, start, holidays)
    count = 0
    day = start
    while day <= end:
        if is_business_day(day, holidays):
            count += 1
        day += timedelta(days=1)
    return count


def add_business_days(start: date, days: int, holidays: Container[date]) -> date:
    """Return the first date whose inclusive business-day count reaches ``days``.

    Args:
        start (date): Reception date (counts as day one when it is a business day).
        days (int): Agreed response time in business days.
        holidays (Container[date]): Non-working dates.

    Returns:
        date: Due date.
    """

    if days <= 0:
        return start
    count = 0
    day = start
    while True:
        if is_business_day(day, holidays):
            count += 1
            if count == days:
                return day
        day += timedelta(days=1)


def clean_code(value: str, field: str = "código") -> str:
    """Normalize a catalog code (lowercase, digits, hyphen or underscore)."""

    code = value.strip().lower()
    if not _CODE_PATTERN.fullmatch(code) or len(code) > 40:
        raise InvalidInputError(
            f"El {field} debe ir en minúsculas, sin tildes ni espacios (ej. en_curso)"
        )
    return code


def clean_color(value: str | None) -> str | None:
    """Validate an optional ``#rrggbb`` color."""

    if value is None or not value.strip():
        return None
    color = value.strip().lower()
    if not _COLOR_PATTERN.fullmatch(color):
        raise InvalidInputError("El color debe tener el formato #rrggbb")
    return color


def clean_prefix(value: str) -> str:
    """Validate a request type prefix used by consecutives (P, Q, R, S, F)."""

    prefix = value.strip().upper()
    if not _PREFIX_PATTERN.fullmatch(prefix):
        raise InvalidInputError("El prefijo admite de 1 a 5 letras sin tildes")
    return prefix


def validate_sla_days(value: int | None, field: str = "tiempo de respuesta") -> int | None:
    """Accept an agreed response time between 0 and 365 business days."""

    if value is None:
        return None
    if value < 0 or value > 365:
        raise InvalidInputError(f"El {field} debe estar entre 0 y 365 días hábiles")
    return value


def validate_quantity(value: int | None) -> int | None:
    """Accept a positive document quantity."""

    if value is None:
        return None
    if value < 1 or value > 100_000:
        raise InvalidInputError("La cantidad debe ser un número entre 1 y 100000")
    return value
