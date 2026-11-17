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
from app.modules.sites.domain.entities import GENERIC_SITE_ABBREVIATION, Site

CASE_ORIGINS = frozenset({"public_form", "internal", "import"})
_CODE_PATTERN = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
_PREFIX_PATTERN = re.compile(r"^[A-Z]{1,5}$")

# Seeded status codes the automations rely on. Statuses stay configurable:
# when one of these is missing or inactive the automation is skipped.
READ_STATUS_CODE = "leida"
IN_PROGRESS_STATUS_CODE = "en_curso"

# How a case ended: JDH answers for the product, or not.
RESOLUTIONS = {"procede": "Procede", "no_procede": "No procede"}
# Request types whose closing must say how it ended (salidas no conformes).
RESOLUTION_REQUIRED_TYPES = frozenset({"queja", "reclamo"})


class EventType:
    """Kinds of entries in the PQRSF history."""

    CREATED = "created"
    UPDATED = "updated"
    ASSIGNED = "assigned"
    STATUS_CHANGED = "status_changed"
    # The responsible's first opening, which moves the case to «Leída».
    READ = "read"
    COMMENT = "comment"
    ITEM_ADDED = "item_added"
    ITEM_UPDATED = "item_updated"
    ITEM_REMOVED = "item_removed"
    DELETED = "deleted"
    # Access records: anyone opening a case, anyone opening the inbox.
    VIEWED = "viewed"
    INBOX_OPENED = "inbox_opened"


# Access records say who looked, not what changed; a repeat within the window is not
# recorded again. Only inbox visits are purged after the retention: openings of a case
# are kept for analytics.
ACCESS_EVENT_TYPES = frozenset({EventType.VIEWED, EventType.INBOX_OPENED})
ACCESS_RETENTION = timedelta(days=90)
ACCESS_REPEAT_WINDOW = timedelta(minutes=30)
# Openings of a case: kept and shown apart («Aperturas»), never in the case timeline.
CASE_OPENING_TYPES = frozenset({EventType.READ, EventType.VIEWED})


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
    # The SIG process of the dependency (action plans): COM, DIS, ALM…
    process_code: str | None = None


@dataclass
class DocumentType:
    """A document a case can request, with its agreed response time."""

    id: UUID
    name: str
    sla_days: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # The answer may be uploaded straight to the case and the email, without going through
    # the document manager (a sensitive document, or one that does not belong there).
    allows_direct_upload: bool = False


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


@dataclass(frozen=True)
class CatalogRef:
    """An entry of a catalog (area, subject, cause) as the case shows it."""

    id: UUID
    code: str
    name: str


@dataclass(frozen=True)
class CaseProduct:
    """A product line of a case: what the requester sent, never edited afterwards.

    ``source`` says where the line came from: ``request`` (public form), ``internal`` or
    ``email``. Description and supplier are copies of the product master at submission.
    """

    id: UUID
    case_id: UUID
    source: str
    invoice_number: str
    sku: str
    lot: str
    expiry_date: date
    affected_quantity: int
    description: str | None
    supplier_code: str | None
    supplier_name: str | None
    sort_order: int


@dataclass
class CaseEvent:
    """An immutable entry of the PQRSF history.

    ``case_id`` is empty for module events (the inbox was opened) and once the
    case is deleted; ``case_code`` keeps the consecutive readable either way.
    """

    id: UUID
    case_id: UUID | None
    event_type: str
    actor_user_id: UUID | None
    from_status_id: UUID | None
    to_status_id: UUID | None
    note: str | None
    data: dict
    created_at: datetime
    case_code: str | None = None


@dataclass(frozen=True)
class ActivityFilters:
    """Filters of the module-wide history, newest first, paged by ``before``."""

    before: datetime | None = None
    limit: int = 50
    event_types: tuple[str, ...] = ()
    actor_user_id: UUID | None = None
    case_code: str | None = None
    include_access: bool = True


@dataclass(frozen=True)
class LastSeen:
    """When a person last opened the PQRSF inbox."""

    user_id: UUID
    last_seen_at: datetime


@dataclass
class TrackingCase:
    """The follow-up of one PQRSF, identified by a consecutive such as P26AC0012.

    ``series`` is the site part of the code (``AC``), fixed when the case is
    opened: moving the case to another site later does not renumber it.
    """

    id: UUID
    code: str
    number: int
    year: int
    series: str
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
    # The seller beyond the typed name: code and email from the sales data, the platform
    # user they match, and where it came from (``invoice``, ``internal`` or ``manual``).
    sales_executive_code: str | None = None
    sales_executive_email: str | None = None
    sales_executive_user_id: UUID | None = None
    sales_executive_source: str | None = None
    # Classification, copied from the request when the case opens: the area the requester
    # picked, the subject and cause, and the route of that cause (``supplier``,
    # ``internal`` or ``none``).
    requested_area: CatalogRef | None = None
    subject_ref: CatalogRef | None = None
    cause_ref: CatalogRef | None = None
    route: str | None = None
    # Follow-up of the products: the pickup order (``yes`` | ``no`` | ``unknown`` and its
    # number, often known later) and when the order was delivered.
    pickup_order_requested: str | None = None
    pickup_order_number: str | None = None
    delivery_date: date | None = None
    products: list[CaseProduct] = field(default_factory=list)
    # When an email from the client or a supplier joined the case and nobody who works it
    # has opened it since; ``None`` once seen. The status and the due date do not change.
    news_at: datetime | None = None
    # How the case ended, set when a complaint or a claim is finished: ``procede`` (JDH
    # answers for the product) or ``no_procede``, and whether a JDH process failed (which
    # calls for a corrective action). Kept when the case is reopened.
    resolution: str | None = None
    internal_failure: bool = False
    resolution_note: str | None = None
    resolved_at: datetime | None = None
    # When an improvement opportunity (OM, P.06) was sent to the supplier; set on its own.
    om_sent_at: datetime | None = None

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
    # Only cases with an email nobody who works them has opened yet.
    with_news: bool = False
    # How the case ended (``procede`` / ``no_procede``), a JDH process failure, an OM sent.
    resolution: str | None = None
    internal_failure: bool | None = None
    om_sent: bool | None = None
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
    # Open cases with news nobody who works them has seen.
    with_news: int = 0
    # Cases with an improvement opportunity sent to the supplier.
    om_sent: int = 0


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


def case_series(site: Site | None) -> str:
    """The site part of a consecutive; cases without a site share ``GN``."""

    return site.abbreviation if site and site.abbreviation else GENERIC_SITE_ABBREVIATION


def case_code(prefix: str, year: int, series: str, number: int) -> str:
    """Format the consecutive: prefix, two-digit year, site series and counter.

    The counter restarts every year for each request type and site, so every
    site keeps its own run of numbers.

    Args:
        prefix (str): Request type prefix, e.g. ``P``.
        year (int): Year of reception.
        series (str): Site abbreviation, e.g. ``AC``.
        number (int): Counter within the type, year and site.

    Returns:
        str: Code such as ``P26AC0012``.
    """

    return f"{prefix}{year % 100:02d}{series}{number:04d}"


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
