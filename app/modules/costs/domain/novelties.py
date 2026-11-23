"""Novelties of the purchase entries and the supplier's credit notes.

A novelty is an entry line that did not match its quotation, kept in the platform so it can
carry one reason and, when the reason calls for it, the supplier's credit note (number,
amount and the PDF or picture of it). The warehouse line is copied when the novelty is first
seen (the daily job, or a person giving a reason to a line); the job refreshes the copy while
the warehouse still re-reads the line (45 days).

The status is never set by hand; it follows from what the novelty has (:func:`derive_status`):

- **anulada**: the warehouse no longer has the line (its document was voided);
- **resuelta**: it has a credit note, or a reason that needs none, or it stopped being a
  novelty before anyone gave it a reason (a quotation was dated back);
- **espera_nota_credito**: its reason needs a credit note that has not come;
- **pendiente**: no reason yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.modules.costs.domain.entries import NO_QUOTATION, NOVELTY, EntryLine

PENDING = "pendiente"
AWAITING_CREDIT_NOTE = "espera_nota_credito"
RESOLVED = "resuelta"
VOIDED = "anulada"
NOVELTY_STATUSES = (PENDING, AWAITING_CREDIT_NOTE, RESOLVED, VOIDED)
NOVELTY_STATUS_LABELS = {
    PENDING: "Pendiente",
    AWAITING_CREDIT_NOTE: "Espera nota crédito",
    RESOLVED: "Resuelta",
    VOIDED: "Anulada",
}

# What the line was when last read: a difference, no quotation, or no longer a novelty.
DIFFERENCE = "diferencia"
WITHOUT_QUOTATION = "sin_cotizacion"
NO_LONGER = "sin_novedad"
KINDS = (DIFFERENCE, WITHOUT_QUOTATION, NO_LONGER)
KIND_LABELS = {
    DIFFERENCE: "Diferencia con la cotización",
    WITHOUT_QUOTATION: "Sin cotización",
    NO_LONGER: "Ya no es novedad",
}

# History of a novelty.
EVENT_DETECTED = "detectada"
EVENT_REASON = "motivo"
EVENT_CREDIT_NOTE = "nota_credito"
EVENT_CREDIT_NOTE_REMOVED = "nota_credito_quitada"
EVENT_VOIDED = "anulada"
EVENT_RESTORED = "reactivada"
EVENT_REREAD = "releida"

NOVELTY_SORTS = ("date", "difference", "difference_pct", "sku", "supplier")
MAX_CREDIT_NOTE_NUMBER = 60
MAX_CREDIT_NOTE_AMOUNT = Decimal("1000000000000")
CREDIT_NOTE_EXTENSIONS = ("pdf", "png", "jpg", "jpeg")


def kind_of(status: str) -> str:
    """The novelty kind of an entry status (``ok`` is no longer a novelty)."""

    if status == NOVELTY:
        return DIFFERENCE
    if status == NO_QUOTATION:
        return WITHOUT_QUOTATION
    return NO_LONGER


def derive_status(
    *, kind: str, voided: bool, reason_needs_credit_note: bool | None, has_credit_note: bool
) -> str:
    """The status a novelty has for what it carries.

    Args:
        kind (str): ``diferencia``, ``sin_cotizacion`` or ``sin_novedad``.
        voided (bool): The warehouse no longer has the line.
        reason_needs_credit_note (bool | None): Whether its reason calls for a credit note;
            ``None`` when it has no reason.
        has_credit_note (bool): A credit note was registered.

    Returns:
        str: ``pendiente``, ``espera_nota_credito``, ``resuelta`` or ``anulada``.
    """

    if voided:
        return VOIDED
    if has_credit_note:
        return RESOLVED
    if reason_needs_credit_note is None:
        return RESOLVED if kind == NO_LONGER else PENDING
    return AWAITING_CREDIT_NOTE if reason_needs_credit_note else RESOLVED


@dataclass(frozen=True)
class Reason:
    """A reason of the catalog («INCREMENTO NO ANUNCIADO»)."""

    id: UUID
    code: str
    name: str
    requires_credit_note: bool
    is_active: bool
    sort_order: int


@dataclass(frozen=True)
class CreditNote:
    """A supplier's credit note as registered against one novelty."""

    id: UUID
    supplier_nit: str
    number: str
    amount: Decimal
    original_filename: str
    content_type: str
    size_bytes: int
    storage_key: str
    registered_by: str
    registered_at: datetime


@dataclass(frozen=True)
class StoredCreditNote:
    """A credit note document (one per supplier NIT and number), shared by its novelties."""

    id: UUID
    supplier_nit: str
    number: str
    original_filename: str
    content_type: str
    size_bytes: int
    storage_key: str
    uploaded_by: str
    created_at: datetime


@dataclass(frozen=True)
class NoveltyEvent:
    id: UUID
    event_type: str
    actor: str | None
    data: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class Novelty:
    """An entry line with a novelty, its reason and its credit note."""

    id: UUID
    line: EntryLine
    kind: str
    status: str
    voided: bool
    reason: Reason | None
    credit_note: CreditNote | None
    reason_by: str | None
    reason_at: datetime | None
    detected_at: datetime
    updated_at: datetime
    events: tuple[NoveltyEvent, ...] = ()


@dataclass(frozen=True)
class NoveltyFilters:
    status: str | None = None
    since: date | None = None
    until: date | None = None
    supplier: str | None = None
    co: str | None = None
    reason_id: UUID | None = None
    kind: str | None = None
    # Reference prefix, description, document or credit note number.
    q: str | None = None


@dataclass(frozen=True)
class NoveltyPage:
    items: tuple[Novelty, ...]
    total: int
    page: int
    size: int
    # By status, for the whole filter except the status itself.
    counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class NoveltyState:
    """What decides a stored novelty's status, without its line."""

    id: UUID
    line_key: str
    kind: str
    voided: bool
    status: str
    reason_needs_credit_note: bool | None
    has_credit_note: bool

    def derived(self, *, kind: str | None = None, voided: bool | None = None) -> str:
        """The status it should have, with a new kind or void mark when given."""

        return derive_status(
            kind=self.kind if kind is None else kind,
            voided=self.voided if voided is None else voided,
            reason_needs_credit_note=self.reason_needs_credit_note,
            has_credit_note=self.has_credit_note,
        )


@dataclass(frozen=True)
class SyncChange:
    """One stored novelty the job touches: its fresh line (``None`` when voided), kind,
    status and the event to write, if any."""

    id: UUID
    line: EntryLine | None
    kind: str
    voided: bool
    status: str
    event: str | None = None


@dataclass(frozen=True)
class SyncReport:
    """What a run of the novelties job did."""

    since: date
    until: date
    lines_read: int
    novelties_seen: int
    created: int
    refreshed: int
    voided: int
    restored: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "since": self.since.isoformat(),
            "until": self.until.isoformat(),
            "lines_read": self.lines_read,
            "novelties_seen": self.novelties_seen,
            "created": self.created,
            "refreshed": self.refreshed,
            "voided": self.voided,
            "restored": self.restored,
        }


__all__ = [
    "AWAITING_CREDIT_NOTE",
    "CREDIT_NOTE_EXTENSIONS",
    "DIFFERENCE",
    "EVENT_CREDIT_NOTE",
    "EVENT_CREDIT_NOTE_REMOVED",
    "EVENT_DETECTED",
    "EVENT_REASON",
    "EVENT_REREAD",
    "EVENT_RESTORED",
    "EVENT_VOIDED",
    "KINDS",
    "KIND_LABELS",
    "MAX_CREDIT_NOTE_AMOUNT",
    "MAX_CREDIT_NOTE_NUMBER",
    "NOVELTY_SORTS",
    "NOVELTY_STATUSES",
    "NOVELTY_STATUS_LABELS",
    "NO_LONGER",
    "PENDING",
    "RESOLVED",
    "VOIDED",
    "WITHOUT_QUOTATION",
    "CreditNote",
    "Novelty",
    "NoveltyEvent",
    "NoveltyFilters",
    "NoveltyPage",
    "NoveltyState",
    "Reason",
    "StoredCreditNote",
    "SyncChange",
    "SyncReport",
    "derive_status",
    "kind_of",
]
