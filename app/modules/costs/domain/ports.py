"""What the costs services need from the outside: the warehouse, the stored novelties, the
file storage and the permission policies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.modules.costs.domain.entries import EntryLine, NoveltyBrief
from app.modules.costs.domain.increases import (
    IncreaseRequest,
    IncreaseRequestPage,
    RequestLine,
    SupplierItem,
    WorkbookRows,
)
from app.modules.costs.domain.novelties import (
    Novelty,
    NoveltyFilters,
    NoveltyPage,
    NoveltyState,
    Reason,
    StoredCreditNote,
    SyncChange,
)
from app.modules.identity.domain.principal import Principal


@dataclass(frozen=True)
class EntryWindow:
    """The purchase entry lines of some days, as the warehouse gave them."""

    lines: tuple[EntryLine, ...]
    # The warehouse publishes the previous prices over the whole history; without them they
    # are worked out inside the window.
    previous_from_warehouse: bool


class EntrySource(Protocol):
    """Purchase entries and the quotation in force on each one's day (the warehouse)."""

    async def lines(self, since: date, until: date) -> EntryWindow: ...

    async def line(self, line_key: str) -> EntryLine | None: ...


class ModulePolicy(Protocol):
    """Who reads and who manages one module of the catalog."""

    async def is_manager(self, principal: Principal) -> bool: ...

    async def can_read(self, principal: Principal) -> bool: ...


class ObjectStorage(Protocol):
    async def put_object(self, key: str, body: bytes, content_type: str | None = None) -> None: ...

    async def get_object(self, key: str) -> bytes: ...

    async def delete_object(self, key: str) -> None: ...


class FileGuard(Protocol):
    """Turns away and cleans the credit note files (type, size, antivirus)."""

    max_attachment_bytes: int

    def refuse(self, name: str, size: int, count: int, total: int) -> str | None: ...

    async def clean(self, name: str, content: bytes) -> tuple[str, str, str, bytes]: ...


@dataclass(frozen=True)
class NewCreditNoteFile:
    """A credit note document to store, already cleaned."""

    id: UUID
    supplier_nit: str
    number: str
    original_filename: str
    content_type: str
    size_bytes: int
    storage_key: str


class NoveltyRepository(Protocol):
    """Stored novelties, their reasons, credit notes and history."""

    async def reasons(self, include_inactive: bool = False) -> list[Reason]: ...

    async def get_reason(self, reason_id: UUID) -> Reason | None: ...

    async def update_reason(
        self,
        reason_id: UUID,
        *,
        name: str | None,
        requires_credit_note: bool | None,
        is_active: bool | None,
    ) -> Reason: ...

    async def briefs_between(self, since: date, until: date) -> dict[str, NoveltyBrief]: ...

    async def get(self, novelty_id: UUID, with_events: bool = False) -> Novelty | None: ...

    async def get_by_line(self, line_key: str) -> Novelty | None: ...

    async def page(
        self, filters: NoveltyFilters, sort: str, descending: bool, page: int, size: int
    ) -> NoveltyPage: ...

    async def create(
        self, line: EntryLine, kind: str, status: str, actor: str | None, now: datetime
    ) -> UUID: ...

    async def set_reason(
        self,
        novelty_id: UUID,
        reason: Reason | None,
        status: str,
        actor: str,
        now: datetime,
        previous: str | None,
    ) -> None: ...

    async def credit_note_by_number(
        self, supplier_nit: str, number: str
    ) -> StoredCreditNote | None: ...

    async def attach_credit_note(
        self,
        novelty_id: UUID,
        *,
        existing: StoredCreditNote | None,
        new_file: NewCreditNoteFile | None,
        amount: Decimal,
        status: str,
        actor: str,
        now: datetime,
    ) -> None: ...

    async def detach_credit_note(
        self, novelty_id: UUID, status: str, actor: str, now: datetime
    ) -> StoredCreditNote | None: ...

    async def states_between(self, since: date, until: date) -> list[NoveltyState]: ...

    async def states_with_reason(self, reason_id: UUID) -> list[NoveltyState]: ...

    async def apply_sync(
        self,
        created: Sequence[tuple[EntryLine, str, str]],
        changed: Sequence[SyncChange],
        now: datetime,
    ) -> None: ...

    async def set_statuses(self, statuses: Mapping[UUID, str], now: datetime) -> None: ...


class WorkbookReader(Protocol):
    """Reads a supplier's returned «MAESTRA» workbook."""

    def read(self, filename: str | None, content: bytes) -> WorkbookRows: ...


class SupplierItemSource(Protocol):
    """Active products of a commercial supplier with their quotation in force (the warehouse)."""

    async def items(
        self, supplier_code: str, skus: list[str] | None = None
    ) -> list[SupplierItem]: ...


@dataclass(frozen=True)
class LedgerEntry:
    """An approved line as the history of increases keeps it."""

    line_id: UUID
    sku: str
    notified_on: date
    increased_on: date
    supplier_sku: str | None
    ean: str | None
    icui_pct: Decimal | None
    ibua: Decimal | None
    old_cost: Decimal | None
    old_cost_with_tax: Decimal | None
    new_cost: Decimal
    new_cost_with_tax: Decimal
    variation_amount: Decimal | None
    variation_pct: Decimal | None
    status: str | None
    observations: str | None
    margin_pct: Decimal | None
    price: Decimal | None
    # ``NACIONAL`` or one regional: a ``RESTO`` line becomes one entry per regional it covers.
    regional: str = "NACIONAL"


class IncreaseLedger(Protocol):
    """The history of increases of the products (``commercial_masters``)."""

    async def margins(self, skus: Sequence[str]) -> dict[str, Decimal | None]: ...

    async def record(
        self, entries: Sequence[LedgerEntry], actor: str, now: datetime
    ) -> dict[UUID, UUID]: ...


class IncreaseRepository(Protocol):
    """The suppliers' increase requests and their lines."""

    async def create(self, request: IncreaseRequest) -> None: ...

    async def page(
        self, status: str | None, supplier: str | None, page: int, size: int
    ) -> IncreaseRequestPage: ...

    async def get(self, request_id: UUID, with_lines: bool = True) -> IncreaseRequest | None: ...

    async def update_line(self, request_id: UUID, line: RequestLine, now: datetime) -> None: ...

    async def decide(
        self,
        request_id: UUID,
        status: str,
        actor: str,
        now: datetime,
        note: str | None,
        ledger_ids: Mapping[UUID, UUID],
    ) -> None: ...


__all__ = [
    "EntrySource",
    "IncreaseLedger",
    "IncreaseRepository",
    "LedgerEntry",
    "SupplierItemSource",
    "WorkbookReader",
    "EntryWindow",
    "FileGuard",
    "ModulePolicy",
    "NewCreditNoteFile",
    "NoveltyRepository",
    "ObjectStorage",
]
