"""In-memory doubles of the Costos ports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.core.errors import DependencyUnavailableError, InvalidInputError, NotFoundError
from app.modules.costs.domain.entries import (
    NO_QUOTATION,
    NOVELTY,
    OK,
    EntryLine,
    NoveltyBrief,
    Placement,
)
from app.modules.costs.domain.novelties import (
    DIFFERENCE,
    EVENT_CREDIT_NOTE,
    EVENT_CREDIT_NOTE_REMOVED,
    EVENT_DETECTED,
    EVENT_REASON,
    NOVELTY_STATUSES,
    WITHOUT_QUOTATION,
    CreditNote,
    Novelty,
    NoveltyEvent,
    NoveltyFilters,
    NoveltyPage,
    NoveltyState,
    Reason,
    StoredCreditNote,
    SyncChange,
)
from app.modules.costs.domain.ports import EntryWindow, NewCreditNoteFile

D = Decimal
NOW = datetime(2026, 11, 23, 15, tzinfo=UTC)
TODAY = date(2026, 11, 23)


def line(
    key: str = "k1",
    *,
    day: date = date(2026, 11, 20),
    sku: str = "1001",
    price: str = "50000",
    quotation: str | None = "50000",
    supplier: str = "399 - UNILEVER ANDINA S.A",
    nit: str | None = "860002518",
    co: str = "003",
    warehouse: str = "BTA9",
    document: str = "EA-60379",
    unit: str = "UNID",
    quantity: str = "10",
    description: str = "Salsa de Tomate Fruco Gx4350g",
    previous: str | None = None,
) -> EntryLine:
    return EntryLine(
        line_key=key,
        entry_date=day,
        co=co,
        regional_code="03",
        regional_name="CENTRO",
        warehouse=warehouse,
        document=document,
        purchase_order="ODC-1",
        invoice_ref="PMP1",
        sku=sku,
        description=description,
        supplier_label=supplier,
        supplier_nit=nit,
        quantity=D(quantity),
        unit=unit,
        unit_price=D(price),
        quotation=None if quotation is None else D(quotation),
        quotation_since=date(2026, 2, 2) if quotation is not None else None,
        discount_pct=D("0"),
        previous_price=None if previous is None else D(previous),
    )


class FakeSource:
    def __init__(self, *lines: EntryLine, previous: bool = True) -> None:
        self.items = list(lines)
        self.previous = previous
        self.reads: list[tuple[date, date]] = []

    async def lines(self, since: date, until: date) -> EntryWindow:
        self.reads.append((since, until))
        chosen = tuple(item for item in self.items if since <= item.entry_date <= until)
        return EntryWindow(chosen, self.previous)

    async def line(self, line_key: str) -> EntryLine | None:
        return next((item for item in self.items if item.line_key == line_key), None)

    async def centers(self) -> dict[str, str]:
        if self.centers_down:
            raise DependencyUnavailableError("down")
        return dict(self.center_names)

    center_names: Mapping[str, str] = {"003": "CENTRO", "001": "ANTIOQUIA"}
    centers_down = False


class FakePolicy:
    def __init__(self, manage: bool = False, read: bool = False) -> None:
        self.manage = manage
        self.read = read or manage

    async def is_manager(self, principal) -> bool:
        return self.manage

    async def can_read(self, principal) -> bool:
        return self.read


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str | None]] = {}

    async def put_object(self, key: str, body: bytes, content_type: str | None = None) -> None:
        self.objects[key] = (body, content_type)

    async def get_object(self, key: str) -> bytes:
        return self.objects[key][0]

    async def delete_object(self, key: str) -> None:
        self.objects.pop(key, None)


class FakeGuard:
    max_attachment_bytes = 1024

    def refuse(self, name: str, size: int, count: int, total: int) -> str | None:
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if extension not in {"pdf", "png", "jpg", "jpeg"}:
            return "tipo de archivo no permitido"
        if size > self.max_attachment_bytes:
            return "supera el tamaño máximo por archivo"
        return None

    async def clean(self, name: str, content: bytes) -> tuple[str, str, str, bytes]:
        extension = name.rsplit(".", 1)[-1].lower()
        if extension == "pdf" and not content.startswith(b"%PDF-"):
            raise InvalidInputError("el contenido no corresponde a su extensión")
        kind = "application/pdf" if extension == "pdf" else f"image/{extension}"
        return name, extension, kind, content


_ENTRY_STATUS = {DIFFERENCE: NOVELTY, WITHOUT_QUOTATION: NO_QUOTATION}


class FakeNoveltyRepository:
    """The repository port over dictionaries."""

    def __init__(self, reasons: Sequence[tuple[str, bool]] = ()) -> None:
        self.reason_rows: dict[UUID, Reason] = {}
        for index, (name, needs) in enumerate(reasons, start=1):
            reason = Reason(uuid4(), name.lower().replace(" ", "-"), name, needs, True, index)
            self.reason_rows[reason.id] = reason
        self.rows: dict[UUID, dict] = {}
        self.notes: dict[UUID, StoredCreditNote] = {}
        self.links: dict[UUID, tuple[UUID, Decimal, str, datetime]] = {}
        self.events: list[tuple[UUID, NoveltyEvent]] = []

    def reason(self, name: str) -> Reason:
        return next(reason for reason in self.reason_rows.values() if reason.name == name)

    def _event(self, novelty_id: UUID, event_type: str, actor, now, **data) -> None:
        self.events.append((novelty_id, NoveltyEvent(uuid4(), event_type, actor, data, now)))

    # Reasons

    async def reasons(self, include_inactive: bool = False) -> list[Reason]:
        items = sorted(self.reason_rows.values(), key=lambda reason: reason.sort_order)
        return [reason for reason in items if include_inactive or reason.is_active]

    async def get_reason(self, reason_id: UUID) -> Reason | None:
        return self.reason_rows.get(reason_id)

    async def update_reason(self, reason_id, *, name, requires_credit_note, is_active) -> Reason:
        reason = self.reason_rows[reason_id]
        changes = {}
        if name is not None:
            changes["name"] = name
        if requires_credit_note is not None:
            changes["requires_credit_note"] = requires_credit_note
        if is_active is not None:
            changes["is_active"] = is_active
        self.reason_rows[reason_id] = replace(reason, **changes)
        return self.reason_rows[reason_id]

    # Reading

    def _novelty(self, novelty_id: UUID, with_events: bool = False) -> Novelty:
        row = self.rows[novelty_id]
        reason = self.reason_rows.get(row["reason_id"]) if row["reason_id"] else None
        note = None
        if novelty_id in self.links:
            note_id, amount, actor, when = self.links[novelty_id]
            stored = self.notes[note_id]
            note = CreditNote(
                stored.id,
                stored.supplier_nit,
                stored.number,
                amount,
                stored.original_filename,
                stored.content_type,
                stored.size_bytes,
                stored.storage_key,
                actor,
                when,
            )
        events = (
            tuple(event for key, event in self.events if key == novelty_id) if with_events else ()
        )
        return Novelty(
            id=novelty_id,
            line=replace(row["line"], status=_ENTRY_STATUS.get(row["kind"], OK)),
            kind=row["kind"],
            status=row["status"],
            voided=row["voided"],
            reason=reason,
            credit_note=note,
            reason_by=row["reason_by"],
            reason_at=row["reason_at"],
            detected_at=row["detected_at"],
            updated_at=row["updated_at"],
            events=events,
        )

    async def briefs_between(self, since: date, until: date) -> dict[str, NoveltyBrief]:
        briefs = {}
        for novelty_id, row in self.rows.items():
            if since <= row["line"].entry_date <= until:
                novelty = self._novelty(novelty_id)
                briefs[row["line"].line_key] = NoveltyBrief(
                    novelty_id,
                    novelty.status,
                    novelty.reason.id if novelty.reason else None,
                    novelty.reason.name if novelty.reason else None,
                    novelty.credit_note.number if novelty.credit_note else None,
                    novelty.credit_note.amount if novelty.credit_note else None,
                )
        return briefs

    async def placements(self, since: date | None, until: date | None) -> list[Placement]:
        found = {}
        for row in self.rows.values():
            item = row["line"]
            if (since and item.entry_date < since) or (until and item.entry_date > until):
                continue
            found[(item.co, item.warehouse)] = Placement(
                item.co, item.warehouse, item.regional_name
            )
        return list(found.values())

    async def get(self, novelty_id: UUID, with_events: bool = False) -> Novelty | None:
        return self._novelty(novelty_id, with_events) if novelty_id in self.rows else None

    async def get_by_line(self, line_key: str) -> Novelty | None:
        for novelty_id, row in self.rows.items():
            if row["line"].line_key == line_key:
                return self._novelty(novelty_id)
        return None

    async def page(self, filters: NoveltyFilters, sort, descending, page, size) -> NoveltyPage:
        items = [self._novelty(novelty_id) for novelty_id in self.rows]

        def keep(novelty: Novelty, with_status: bool) -> bool:
            if with_status and filters.status and novelty.status != filters.status:
                return False
            if filters.supplier and novelty.line.supplier_code != filters.supplier:
                return False
            if filters.warehouse and novelty.line.warehouse != filters.warehouse.upper():
                return False
            if filters.reason_id and (not novelty.reason or novelty.reason.id != filters.reason_id):
                return False
            if filters.since and novelty.line.entry_date < filters.since:
                return False
            if filters.until and novelty.line.entry_date > filters.until:
                return False
            return not filters.q or novelty.line.sku.startswith(filters.q)

        counted = [item for item in items if keep(item, False)]
        counts = {
            status: sum(1 for item in counted if item.status == status)
            for status in NOVELTY_STATUSES
        }
        counts["total"] = len(counted)
        chosen = sorted(
            (item for item in items if keep(item, True)),
            key=lambda item: item.line.entry_date,
            reverse=descending,
        )
        start = (page - 1) * size
        return NoveltyPage(tuple(chosen[start : start + size]), len(chosen), page, size, counts)

    # Writing

    async def create(self, line: EntryLine, kind: str, status: str, actor, now) -> UUID:
        novelty_id = uuid4()
        self.rows[novelty_id] = {
            "line": line,
            "kind": kind,
            "status": status,
            "voided": False,
            "reason_id": None,
            "reason_by": None,
            "reason_at": None,
            "detected_at": now,
            "updated_at": now,
        }
        self._event(novelty_id, EVENT_DETECTED, actor, now, kind=kind)
        return novelty_id

    async def set_reason(self, novelty_id, reason, status, actor, now, previous) -> None:
        row = self.rows[novelty_id]
        row.update(
            reason_id=reason.id if reason else None,
            reason_by=actor if reason else None,
            reason_at=now if reason else None,
            status=status,
            updated_at=now,
        )
        self._event(
            novelty_id,
            EVENT_REASON,
            actor,
            now,
            reason=reason.name if reason else None,
            previous=previous,
        )

    async def credit_note_by_number(
        self, supplier_nit: str, number: str
    ) -> StoredCreditNote | None:
        return next(
            (
                note
                for note in self.notes.values()
                if note.supplier_nit == supplier_nit and note.number.upper() == number.upper()
            ),
            None,
        )

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
    ) -> None:
        if new_file is not None:
            note = StoredCreditNote(
                new_file.id,
                new_file.supplier_nit,
                new_file.number,
                new_file.original_filename,
                new_file.content_type,
                new_file.size_bytes,
                new_file.storage_key,
                actor,
                now,
            )
            self.notes[note.id] = note
        else:
            note = existing
        self.links[novelty_id] = (note.id, amount, actor, now)
        self.rows[novelty_id].update(status=status, updated_at=now)
        self._event(
            novelty_id, EVENT_CREDIT_NOTE, actor, now, number=note.number, amount=str(amount)
        )

    async def detach_credit_note(self, novelty_id, status, actor, now) -> StoredCreditNote | None:
        if novelty_id not in self.links:
            raise NotFoundError("La novedad no tiene nota crédito")
        note_id, *_ = self.links.pop(novelty_id)
        self.rows[novelty_id].update(status=status, updated_at=now)
        self._event(novelty_id, EVENT_CREDIT_NOTE_REMOVED, actor, now)
        if any(link[0] == note_id for link in self.links.values()):
            return None
        return self.notes.pop(note_id)

    def _state(self, novelty_id: UUID) -> NoveltyState:
        row = self.rows[novelty_id]
        reason = self.reason_rows.get(row["reason_id"]) if row["reason_id"] else None
        return NoveltyState(
            novelty_id,
            row["line"].line_key,
            row["kind"],
            row["voided"],
            row["status"],
            reason.requires_credit_note if reason else None,
            novelty_id in self.links,
        )

    async def states_between(self, since: date, until: date) -> list[NoveltyState]:
        return [
            self._state(novelty_id)
            for novelty_id, row in self.rows.items()
            if since <= row["line"].entry_date <= until
        ]

    async def states_with_reason(self, reason_id: UUID) -> list[NoveltyState]:
        return [
            self._state(novelty_id)
            for novelty_id, row in self.rows.items()
            if row["reason_id"] == reason_id
        ]

    async def apply_sync(self, created, changed: Sequence[SyncChange], now: datetime) -> None:
        for line, kind, status in created:
            await self.create(line, kind, status, None, now)
        for change in changed:
            row = self.rows[change.id]
            if change.line is not None:
                row["line"] = change.line
            row.update(kind=change.kind, voided=change.voided, status=change.status)
            if change.event:
                self._event(change.id, change.event, None, now, kind=change.kind)

    async def set_statuses(self, statuses: Mapping[UUID, str], now: datetime) -> None:
        for novelty_id, status in statuses.items():
            self.rows[novelty_id].update(status=status, updated_at=now)
