"""PostgreSQL repository of the novelties, their reasons and credit notes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, case, delete, func, insert, or_, select, update

from app.core.errors import NotFoundError
from app.infrastructure.database import Database
from app.modules.costs.domain.entries import (
    NO_QUOTATION,
    NOVELTY,
    OK,
    EntryLine,
    NoveltyBrief,
    difference_pct,
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
from app.modules.costs.domain.ports import NewCreditNoteFile
from app.modules.costs.infrastructure.models import (
    CostCreditNoteModel,
    CostNoveltyCreditNoteModel,
    CostNoveltyEventModel,
    CostNoveltyModel,
    CostReasonModel,
)

N = CostNoveltyModel
R = CostReasonModel
C = CostCreditNoteModel
L = CostNoveltyCreditNoteModel
E = CostNoveltyEventModel
_CHUNK = 1000
_ENTRY_STATUS = {DIFFERENCE: NOVELTY, WITHOUT_QUOTATION: NO_QUOTATION}


def _reason(model: CostReasonModel | None) -> Reason | None:
    if model is None:
        return None
    return Reason(
        id=model.id,
        code=model.code,
        name=model.name,
        requires_credit_note=model.requires_credit_note,
        is_active=model.is_active,
        sort_order=model.sort_order,
    )


def line_values(line: EntryLine) -> dict[str, Any]:
    """The columns of a novelty that copy the warehouse line."""

    difference = None if line.quotation is None else line.unit_price - line.quotation
    pct = difference_pct(line.unit_price, line.quotation)
    return {
        "line_key": line.line_key,
        "entry_date": line.entry_date,
        "co": line.co,
        "regional_code": line.regional_code,
        "regional_name": line.regional_name,
        "warehouse": line.warehouse,
        "document": line.document,
        "purchase_order": line.purchase_order,
        "invoice_ref": line.invoice_ref,
        "sku": line.sku,
        "description": line.description,
        "supplier_label": line.supplier_label,
        "supplier_code": line.supplier_code,
        "supplier_nit": line.supplier_nit,
        "quantity": line.quantity,
        "unit": line.unit,
        "unit_price": line.unit_price,
        "quotation": line.quotation,
        "quotation_since": line.quotation_since,
        "discount_pct": line.discount_pct,
        "quotation_list_price": line.quotation_list_price,
        "difference": difference,
        "difference_pct": None if pct is None else round(pct, 6),
        "previous_price": line.previous_price,
        "antepenultimate_price": line.antepenultimate_price,
    }


def _line(model: CostNoveltyModel) -> EntryLine:
    return EntryLine(
        line_key=model.line_key,
        entry_date=model.entry_date,
        co=model.co,
        regional_code=model.regional_code,
        regional_name=model.regional_name,
        warehouse=model.warehouse,
        document=model.document,
        purchase_order=model.purchase_order,
        invoice_ref=model.invoice_ref,
        sku=model.sku,
        description=model.description,
        supplier_label=model.supplier_label,
        supplier_nit=model.supplier_nit,
        quantity=model.quantity,
        unit=model.unit,
        unit_price=model.unit_price,
        quotation=model.quotation,
        quotation_since=model.quotation_since,
        discount_pct=model.discount_pct,
        quotation_list_price=model.quotation_list_price,
        previous_price=model.previous_price,
        antepenultimate_price=model.antepenultimate_price,
        status=_ENTRY_STATUS.get(model.kind, OK),
    )


def _credit_note(
    link: CostNoveltyCreditNoteModel | None, note: CostCreditNoteModel | None
) -> CreditNote | None:
    if link is None or note is None:
        return None
    return CreditNote(
        id=note.id,
        supplier_nit=note.supplier_nit,
        number=note.number,
        amount=link.amount,
        original_filename=note.original_filename,
        content_type=note.content_type,
        size_bytes=note.size_bytes,
        storage_key=note.storage_key,
        registered_by=link.registered_by,
        registered_at=link.registered_at,
    )


def _stored(note: CostCreditNoteModel) -> StoredCreditNote:
    return StoredCreditNote(
        id=note.id,
        supplier_nit=note.supplier_nit,
        number=note.number,
        original_filename=note.original_filename,
        content_type=note.content_type,
        size_bytes=note.size_bytes,
        storage_key=note.storage_key,
        uploaded_by=note.uploaded_by,
        created_at=note.created_at,
    )


def _novelty(
    model: CostNoveltyModel,
    reason: CostReasonModel | None,
    link: CostNoveltyCreditNoteModel | None,
    note: CostCreditNoteModel | None,
    events: tuple[NoveltyEvent, ...] = (),
) -> Novelty:
    return Novelty(
        id=model.id,
        line=_line(model),
        kind=model.kind,
        status=model.status,
        voided=model.voided,
        reason=_reason(reason),
        credit_note=_credit_note(link, note),
        reason_by=model.reason_by,
        reason_at=model.reason_at,
        detected_at=model.detected_at,
        updated_at=model.updated_at,
        events=events,
    )


def _event(
    novelty_id: UUID, event_type: str, actor: str | None, now: datetime, **data: Any
) -> dict:
    return {
        "id": uuid4(),
        "novelty_id": novelty_id,
        "event_type": event_type,
        "actor": actor,
        "data": {key: _jsonable(value) for key, value in data.items()},
        "created_at": now,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _joined():
    return (
        N.__table__.outerjoin(R, R.id == N.reason_id)
        .outerjoin(L, L.novelty_id == N.id)
        .outerjoin(C, C.id == L.credit_note_id)
    )


_SORTS = {
    "date": (N.entry_date, N.document, N.line_key),
    "difference": (func.abs(N.difference),),
    "difference_pct": (func.abs(N.difference_pct),),
    "sku": (N.sku, N.entry_date),
    "supplier": (N.supplier_label, N.entry_date),
}


class SqlAlchemyNoveltyRepository:
    """Novelties keyed by the warehouse line, with their reason, credit note and history."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): The platform database.
        """

        self.database = database

    # --- Reasons ---

    async def reasons(self, include_inactive: bool = False) -> list[Reason]:
        """The reasons in their order.

        Args:
            include_inactive (bool): Also the switched-off ones.

        Returns:
            list[Reason]: The catalog.
        """

        statement = select(R).order_by(R.sort_order, R.name)
        if not include_inactive:
            statement = statement.where(R.is_active.is_(True))
        async with self.database.session_factory() as session:
            return [_reason(model) for model in await session.scalars(statement)]

    async def get_reason(self, reason_id: UUID) -> Reason | None:
        """One reason, or ``None``."""

        async with self.database.session_factory() as session:
            return _reason(await session.get(R, reason_id))

    async def update_reason(
        self,
        reason_id: UUID,
        *,
        name: str | None,
        requires_credit_note: bool | None,
        is_active: bool | None,
    ) -> Reason:
        """Change the given fields of a reason.

        Raises:
            NotFoundError: No such reason.
        """

        async with self.database.session_factory() as session, session.begin():
            model = await session.get(R, reason_id)
            if model is None:
                raise NotFoundError("Motivo no encontrado")
            if name is not None:
                model.name = name
            if requires_credit_note is not None:
                model.requires_credit_note = requires_credit_note
            if is_active is not None:
                model.is_active = is_active
            model.updated_at = func.now()
        return await self.get_reason(reason_id)  # type: ignore[return-value]

    # --- Reading novelties ---

    async def briefs_between(self, since: date, until: date) -> dict[str, NoveltyBrief]:
        """What the platform keeps of the lines of some days, by line key.

        Args:
            since (date): First entry day.
            until (date): Last entry day.

        Returns:
            dict[str, NoveltyBrief]: Line key → status, reason and credit note.
        """

        statement = (
            select(N.line_key, N.id, N.status, N.reason_id, R.name, C.number, L.amount)
            .select_from(_joined())
            .where(N.entry_date.between(since, until))
        )
        async with self.database.session_factory() as session:
            rows = (await session.execute(statement)).all()
        return {
            key: NoveltyBrief(novelty_id, status, reason_id, reason_name, number, amount)
            for key, novelty_id, status, reason_id, reason_name, number, amount in rows
        }

    async def _events(self, session, novelty_id: UUID) -> tuple[NoveltyEvent, ...]:
        rows = await session.scalars(
            select(E).where(E.novelty_id == novelty_id).order_by(E.created_at, E.id)
        )
        return tuple(
            NoveltyEvent(
                event.id, event.event_type, event.actor, dict(event.data or {}), event.created_at
            )
            for event in rows
        )

    async def _one(self, condition, with_events: bool) -> Novelty | None:
        statement = select(N, R, L, C).select_from(_joined()).where(condition)
        async with self.database.session_factory() as session:
            row = (await session.execute(statement)).first()
            if row is None:
                return None
            model, reason, link, note = row
            events = await self._events(session, model.id) if with_events else ()
        return _novelty(model, reason, link, note, events)

    async def get(self, novelty_id: UUID, with_events: bool = False) -> Novelty | None:
        """One novelty, with its history when asked."""

        return await self._one(N.id == novelty_id, with_events)

    async def get_by_line(self, line_key: str) -> Novelty | None:
        """The novelty of a warehouse line, or ``None``."""

        return await self._one(N.line_key == line_key, False)

    def _conditions(self, filters: NoveltyFilters, with_status: bool) -> list:
        conditions: list = []
        if with_status and filters.status:
            conditions.append(N.status == filters.status)
        if filters.since:
            conditions.append(N.entry_date >= filters.since)
        if filters.until:
            conditions.append(N.entry_date <= filters.until)
        if filters.supplier:
            conditions.append(N.supplier_code == filters.supplier.strip())
        if filters.co:
            conditions.append(func.ltrim(N.co, "0") == filters.co.strip().lstrip("0"))
        if filters.reason_id:
            conditions.append(N.reason_id == filters.reason_id)
        if filters.kind:
            conditions.append(N.kind == filters.kind)
        term = (filters.q or "").strip()
        if term:
            conditions.append(
                or_(
                    N.sku.startswith(term, autoescape=True),
                    N.description.icontains(term, autoescape=True),
                    N.document.icontains(term, autoescape=True),
                    C.number.icontains(term, autoescape=True),
                )
            )
        return conditions

    async def page(
        self, filters: NoveltyFilters, sort: str, descending: bool, page: int, size: int
    ) -> NoveltyPage:
        """A page of the filter with the counts by status (of the filter without its status).

        Args:
            filters (NoveltyFilters): What to narrow by.
            sort (str): ``date``, ``difference``, ``difference_pct``, ``sku`` or ``supplier``.
            descending (bool): Latest or largest first.
            page (int): Page, from 1.
            size (int): Rows per page.

        Returns:
            NoveltyPage: The rows, the total and the counts.
        """

        joined = _joined()
        listing = select(N, R, L, C).select_from(joined)
        conditions = self._conditions(filters, with_status=True)
        if conditions:
            listing = listing.where(and_(*conditions))
        counting_conditions = self._conditions(filters, with_status=False)
        counting = select(
            *(func.count(case((N.status == status, 1))) for status in NOVELTY_STATUSES)
        ).select_from(joined)
        if counting_conditions:
            counting = counting.where(and_(*counting_conditions))
        total_query = select(func.count()).select_from(joined)
        if conditions:
            total_query = total_query.where(and_(*conditions))
        columns = _SORTS[sort]
        order = [(column.desc() if descending else column.asc()).nulls_last() for column in columns]
        listing = listing.order_by(*order, N.id).offset((page - 1) * size).limit(size)
        async with self.database.session_factory() as session:
            total = (await session.execute(total_query)).scalar_one()
            by_status = (await session.execute(counting)).one()
            rows = (await session.execute(listing)).all()
        counts = dict(zip(NOVELTY_STATUSES, (int(value) for value in by_status), strict=True))
        counts["total"] = sum(counts.values())
        return NoveltyPage(
            items=tuple(_novelty(*row) for row in rows),
            total=int(total),
            page=page,
            size=size,
            counts=counts,
        )

    # --- Writing novelties ---

    async def create(
        self, line: EntryLine, kind: str, status: str, actor: str | None, now: datetime
    ) -> UUID:
        """Keep a warehouse line as a novelty.

        Args:
            line (EntryLine): The warehouse line.
            kind (str): ``diferencia``, ``sin_cotizacion`` or ``sin_novedad``.
            status (str): Its status.
            actor (str | None): Who asked (``None``: the daily job).
            now (datetime): When.

        Returns:
            UUID: The novelty.
        """

        novelty_id = uuid4()
        async with self.database.session_factory() as session, session.begin():
            await session.execute(
                insert(N).values(
                    id=novelty_id,
                    kind=kind,
                    status=status,
                    voided=False,
                    detected_at=now,
                    synced_at=now,
                    updated_at=now,
                    **line_values(line),
                )
            )
            await session.execute(
                insert(E).values(**_event(novelty_id, EVENT_DETECTED, actor, now, kind=kind))
            )
        return novelty_id

    async def set_reason(
        self,
        novelty_id: UUID,
        reason: Reason | None,
        status: str,
        actor: str,
        now: datetime,
        previous: str | None,
    ) -> None:
        """Store a novelty's reason (or none) and its status, and write it in the history."""

        async with self.database.session_factory() as session, session.begin():
            await session.execute(
                update(N)
                .where(N.id == novelty_id)
                .values(
                    reason_id=reason.id if reason else None,
                    reason_by=actor if reason else None,
                    reason_at=now if reason else None,
                    status=status,
                    updated_at=now,
                )
            )
            await session.execute(
                insert(E).values(
                    **_event(
                        novelty_id,
                        EVENT_REASON,
                        actor,
                        now,
                        reason=reason.name if reason else None,
                        previous=previous,
                        status=status,
                    )
                )
            )

    async def credit_note_by_number(
        self, supplier_nit: str, number: str
    ) -> StoredCreditNote | None:
        """The credit note a supplier already has with this number (any case)."""

        async with self.database.session_factory() as session:
            model = await session.scalar(
                select(C).where(
                    C.supplier_nit == supplier_nit, func.upper(C.number) == number.upper()
                )
            )
        return _stored(model) if model is not None else None

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
        """Register a credit note against a novelty (storing its document when it is new)."""

        async with self.database.session_factory() as session, session.begin():
            if new_file is not None:
                await session.execute(
                    insert(C).values(
                        id=new_file.id,
                        supplier_nit=new_file.supplier_nit,
                        number=new_file.number,
                        original_filename=new_file.original_filename,
                        content_type=new_file.content_type,
                        size_bytes=new_file.size_bytes,
                        storage_key=new_file.storage_key,
                        uploaded_by=actor,
                        created_at=now,
                    )
                )
                note_id, number = new_file.id, new_file.number
            elif existing is not None:
                note_id, number = existing.id, existing.number
            else:  # pragma: no cover - the service always passes one
                raise ValueError("A credit note needs a document")
            await session.execute(
                insert(L).values(
                    novelty_id=novelty_id,
                    credit_note_id=note_id,
                    amount=amount,
                    registered_by=actor,
                    registered_at=now,
                )
            )
            await session.execute(
                update(N).where(N.id == novelty_id).values(status=status, updated_at=now)
            )
            await session.execute(
                insert(E).values(
                    **_event(
                        novelty_id,
                        EVENT_CREDIT_NOTE,
                        actor,
                        now,
                        number=number,
                        amount=amount,
                        reused=existing is not None,
                        status=status,
                    )
                )
            )

    async def detach_credit_note(
        self, novelty_id: UUID, status: str, actor: str, now: datetime
    ) -> StoredCreditNote | None:
        """Take the credit note off a novelty; delete the document when nothing else uses it.

        Returns:
            StoredCreditNote | None: The deleted document (its file must go too), or ``None``.
        """

        async with self.database.session_factory() as session, session.begin():
            link = await session.get(L, novelty_id)
            if link is None:
                raise NotFoundError("La novedad no tiene nota crédito")
            note = await session.get(C, link.credit_note_id)
            await session.execute(delete(L).where(L.novelty_id == novelty_id))
            others = await session.scalar(
                select(func.count()).select_from(L).where(L.credit_note_id == link.credit_note_id)
            )
            orphan = None
            if note is not None and not others:
                orphan = _stored(note)
                await session.execute(delete(C).where(C.id == note.id))
            await session.execute(
                update(N).where(N.id == novelty_id).values(status=status, updated_at=now)
            )
            await session.execute(
                insert(E).values(
                    **_event(
                        novelty_id,
                        EVENT_CREDIT_NOTE_REMOVED,
                        actor,
                        now,
                        number=note.number if note else None,
                        amount=link.amount,
                        status=status,
                    )
                )
            )
        return orphan

    # --- The daily job ---

    def _states(self):
        return select(
            N.id,
            N.line_key,
            N.kind,
            N.voided,
            N.status,
            R.requires_credit_note,
            L.novelty_id.is_not(None),
        ).select_from(
            N.__table__.outerjoin(R, R.id == N.reason_id).outerjoin(L, L.novelty_id == N.id)
        )

    async def states_between(self, since: date, until: date) -> list[NoveltyState]:
        """What decides the status of the novelties of some entry days."""

        async with self.database.session_factory() as session:
            rows = (
                await session.execute(self._states().where(N.entry_date.between(since, until)))
            ).all()
        return [NoveltyState(*row) for row in rows]

    async def states_with_reason(self, reason_id: UUID) -> list[NoveltyState]:
        """What decides the status of the novelties that carry a reason."""

        async with self.database.session_factory() as session:
            rows = (await session.execute(self._states().where(N.reason_id == reason_id))).all()
        return [NoveltyState(*row) for row in rows]

    async def apply_sync(
        self,
        created: Sequence[tuple[EntryLine, str, str]],
        changed: Sequence[SyncChange],
        now: datetime,
    ) -> None:
        """Write a run of the job in one transaction: new novelties, refreshed copies, void
        marks and their history."""

        new_rows: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        for line, kind, status in created:
            novelty_id = uuid4()
            new_rows.append(
                {
                    "id": novelty_id,
                    "kind": kind,
                    "status": status,
                    "voided": False,
                    "detected_at": now,
                    "synced_at": now,
                    "updated_at": now,
                    **line_values(line),
                }
            )
            events.append(_event(novelty_id, EVENT_DETECTED, None, now, kind=kind))
        refreshed: list[dict[str, Any]] = []
        voided: list[dict[str, Any]] = []
        for change in changed:
            values = {
                "id": change.id,
                "kind": change.kind,
                "voided": change.voided,
                "status": change.status,
            }
            if change.line is not None:
                line = line_values(change.line)
                line.pop("line_key")
                refreshed.append({**values, **line, "synced_at": now})
            else:
                voided.append({**values, "updated_at": now})
            if change.event is not None:
                events.append(
                    _event(
                        change.id, change.event, None, now, kind=change.kind, status=change.status
                    )
                )
        async with self.database.session_factory() as session, session.begin():
            for start in range(0, len(new_rows), _CHUNK):
                await session.execute(insert(N), new_rows[start : start + _CHUNK])
            # ORM bulk updates by primary key.
            for start in range(0, len(refreshed), _CHUNK):
                await session.execute(update(N), refreshed[start : start + _CHUNK])
            for start in range(0, len(voided), _CHUNK):
                await session.execute(update(N), voided[start : start + _CHUNK])
            for start in range(0, len(events), _CHUNK):
                await session.execute(insert(E), events[start : start + _CHUNK])

    async def set_statuses(self, statuses: Mapping[UUID, str], now: datetime) -> None:
        """Store new statuses (a reason changed whether it needs a credit note)."""

        if not statuses:
            return
        rows = [{"id": key, "status": value, "updated_at": now} for key, value in statuses.items()]
        async with self.database.session_factory() as session, session.begin():
            for start in range(0, len(rows), _CHUNK):
                await session.execute(update(N), rows[start : start + _CHUNK])


__all__ = ["SqlAlchemyNoveltyRepository", "line_values"]
