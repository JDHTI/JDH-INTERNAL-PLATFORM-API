"""Novelties of the purchase entries: their reason, the supplier's credit note and the daily
job that copies them from the warehouse."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.modules.costs.application.access import CostAccess
from app.modules.costs.application.entries import center_names
from app.modules.costs.domain.entries import (
    EntryLine,
    FilterOptions,
    entry_status,
    fill_previous_prices,
    filter_options,
    with_status,
)
from app.modules.costs.domain.modules import ENTRIES, NOVELTIES
from app.modules.costs.domain.novelties import (
    CREDIT_NOTE_EXTENSIONS,
    EVENT_REREAD,
    EVENT_RESTORED,
    EVENT_VOIDED,
    KINDS,
    MAX_CREDIT_NOTE_AMOUNT,
    MAX_CREDIT_NOTE_NUMBER,
    NO_LONGER,
    NOVELTY_SORTS,
    NOVELTY_STATUSES,
    CreditNote,
    Novelty,
    NoveltyFilters,
    NoveltyPage,
    Reason,
    SyncChange,
    SyncReport,
    derive_status,
    kind_of,
)
from app.modules.costs.domain.ports import (
    EntrySource,
    FileGuard,
    NewCreditNoteFile,
    NoveltyRepository,
    ObjectStorage,
)
from app.modules.identity.domain.principal import Principal

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 200
MAX_REASON_NAME = 120
_BOGOTA = ZoneInfo("America/Bogota")


@dataclass(frozen=True)
class UploadedFile:
    filename: str
    content: bytes


def clean_amount(value: Any) -> Decimal:
    """The credit note's amount: a positive number of pesos.

    Raises:
        InvalidInputError: Not a number, zero, negative or absurdly large.
    """

    try:
        amount = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidInputError("El valor de la nota crédito no es un número") from exc
    if not amount.is_finite() or amount <= 0:
        raise InvalidInputError("El valor de la nota crédito debe ser mayor que cero")
    if amount >= MAX_CREDIT_NOTE_AMOUNT:
        raise InvalidInputError("El valor de la nota crédito es demasiado grande")
    return amount.quantize(Decimal("0.01"))


def clean_number(value: str | None) -> str:
    """The credit note's number as the supplier wrote it, without surrounding spaces.

    Raises:
        InvalidInputError: Empty or too long.
    """

    number = " ".join((value or "").split())
    if not number:
        raise InvalidInputError("Escribe el número de la nota crédito")
    if len(number) > MAX_CREDIT_NOTE_NUMBER:
        raise InvalidInputError(
            f"El número de la nota crédito pasa de {MAX_CREDIT_NOTE_NUMBER} caracteres"
        )
    return number


class NoveltyService:
    """Novelties of the purchase entries (``novedades-compras``)."""

    def __init__(
        self,
        repository: NoveltyRepository,
        source: EntrySource,
        access: CostAccess,
        storage: ObjectStorage,
        guard: FileGuard,
        threshold: Decimal,
        sync_days: int,
        start_date: date,
        today: Callable[[], date] = lambda: datetime.now(_BOGOTA).date(),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create the service.

        Args:
            repository (NoveltyRepository): Stored novelties, reasons and credit notes.
            source (EntrySource): The warehouse's entries and quotations.
            access (CostAccess): Who reads and manages each Costos key.
            storage (ObjectStorage): Where the credit note files go.
            guard (FileGuard): Type, size and antivirus of the credit note files.
            threshold (Decimal): Largest relative difference that is no novelty.
            sync_days (int): Days back the job re-reads (the warehouse re-reads 45).
            start_date (date): The job never copies lines older than this.
            today (Callable[[], date]): Today in Bogotá.
            clock (Callable[[], datetime]): Now, in UTC.
        """

        self.repository = repository
        self.source = source
        self.access = access
        self.storage = storage
        self.guard = guard
        self.threshold = threshold
        self.sync_days = sync_days
        self.start_date = start_date
        self.today = today
        self.clock = clock

    # --- Reasons ---

    async def reasons(self, include_inactive: bool = False) -> list[Reason]:
        """The reasons of the catalog, in their order (the active ones unless asked)."""

        return await self.repository.reasons(include_inactive)

    async def update_reason(
        self,
        principal: Principal,
        reason_id: UUID,
        *,
        name: str | None = None,
        requires_credit_note: bool | None = None,
        is_active: bool | None = None,
    ) -> Reason:
        """Rename a reason, say whether it needs a credit note, or switch it off.

        Changing whether it needs a credit note moves the novelties that carry it between
        «espera nota crédito» and «resuelta».

        Raises:
            ForbiddenError: The caller does not manage the novelties.
            InvalidInputError: Empty or too long name.
            NotFoundError: No such reason.
        """

        await self.access.require_manage(
            principal, NOVELTIES, message="No tienes permiso para cambiar los motivos"
        )
        if name is not None:
            name = " ".join(name.split()).upper()
            if not name or len(name) > MAX_REASON_NAME:
                raise InvalidInputError(
                    f"El nombre del motivo debe tener entre 1 y {MAX_REASON_NAME} caracteres"
                )
        if await self.repository.get_reason(reason_id) is None:
            raise NotFoundError("Motivo no encontrado")
        reason = await self.repository.update_reason(
            reason_id, name=name, requires_credit_note=requires_credit_note, is_active=is_active
        )
        if requires_credit_note is not None:
            states = await self.repository.states_with_reason(reason_id)
            moved = {
                state.id: status
                for state in states
                if (
                    status := derive_status(
                        kind=state.kind,
                        voided=state.voided,
                        reason_needs_credit_note=requires_credit_note,
                        has_credit_note=state.has_credit_note,
                    )
                )
                != state.status
            }
            await self.repository.set_statuses(moved, self.clock())
        return reason

    # --- Reading ---

    async def page(
        self, filters: NoveltyFilters, sort: str, descending: bool, page: int, size: int
    ) -> NoveltyPage:
        """A page of the novelties with the counts by status of the filter.

        Raises:
            InvalidInputError: Bad status, kind, sort, period or page.
        """

        if page < 1 or not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"Página inválida; el tamaño máximo es {MAX_PAGE_SIZE}")
        if filters.status is not None and filters.status not in NOVELTY_STATUSES:
            raise InvalidInputError("Estado desconocido")
        if filters.kind is not None and filters.kind not in KINDS:
            raise InvalidInputError("Tipo de novedad desconocido")
        if sort not in NOVELTY_SORTS:
            raise InvalidInputError("Orden desconocido")
        if filters.since and filters.until and filters.since > filters.until:
            raise InvalidInputError("La fecha inicial es posterior a la final")
        return await self.repository.page(filters, sort, descending, page, size)

    async def options(self, since: date | None, until: date | None) -> FilterOptions:
        """The operation centers and warehouses of the novelties of the period (the tray's
        dropdowns); without dates, of every novelty.

        Raises:
            InvalidInputError: The period ends before it starts.
        """

        if since and until and since > until:
            raise InvalidInputError("La fecha inicial es posterior a la final")
        places = await self.repository.placements(since, until)
        return filter_options(places, await center_names(self.source))

    async def get(self, novelty_id: UUID) -> Novelty:
        """A novelty with its history.

        Raises:
            NotFoundError: No such novelty.
        """

        novelty = await self.repository.get(novelty_id, with_events=True)
        if novelty is None:
            raise NotFoundError("Novedad no encontrada")
        return novelty

    # --- Reason of a novelty ---

    async def _reason(self, reason_id: UUID | None) -> Reason | None:
        if reason_id is None:
            return None
        reason = await self.repository.get_reason(reason_id)
        if reason is None:
            raise NotFoundError("Motivo no encontrado")
        if not reason.is_active:
            raise InvalidInputError("El motivo está inactivo")
        return reason

    async def _require_reason_setter(self, principal: Principal) -> None:
        await self.access.require_manage(
            principal,
            ENTRIES,
            NOVELTIES,
            message="No tienes permiso para asignar motivos a las entradas",
        )

    async def _give_reason(
        self, principal: Principal, novelty: Novelty, reason: Reason | None
    ) -> Novelty:
        current = novelty.reason.id if novelty.reason else None
        if current == (reason.id if reason else None):
            return novelty
        status = derive_status(
            kind=novelty.kind,
            voided=novelty.voided,
            reason_needs_credit_note=reason.requires_credit_note if reason else None,
            has_credit_note=novelty.credit_note is not None,
        )
        await self.repository.set_reason(
            novelty.id,
            reason,
            status,
            principal.email,
            self.clock(),
            previous=novelty.reason.name if novelty.reason else None,
        )
        return await self.get(novelty.id)

    async def set_reason(
        self, principal: Principal, novelty_id: UUID, reason_id: UUID | None
    ) -> Novelty:
        """Give a novelty its one reason, or take it away (``None``).

        Raises:
            ForbiddenError: The caller manages neither the entries nor the novelties.
            NotFoundError: No such novelty or reason.
            InvalidInputError: The reason is switched off.
        """

        await self._require_reason_setter(principal)
        novelty = await self.get(novelty_id)
        return await self._give_reason(principal, novelty, await self._reason(reason_id))

    async def set_reason_for_line(
        self, principal: Principal, line_key: str, reason_id: UUID | None
    ) -> Novelty:
        """Give an entry line its reason from the analysis.

        A line the platform does not keep yet is read from the warehouse and kept as a novelty
        (even one within the threshold: the person saw something in it).

        Raises:
            ForbiddenError: The caller manages neither the entries nor the novelties.
            NotFoundError: No such line in the warehouse, or no such reason.
            InvalidInputError: The reason is switched off.
        """

        await self._require_reason_setter(principal)
        reason = await self._reason(reason_id)
        novelty = await self.repository.get_by_line(line_key)
        if novelty is None:
            if reason is None:
                raise NotFoundError("La entrada no tiene motivo que quitar")
            line = await self.source.line(line_key)
            if line is None:
                raise NotFoundError("Entrada no encontrada en el DWH")
            status = entry_status(line.unit_price, line.quotation, self.threshold)
            kind = kind_of(status)
            created = derive_status(
                kind=kind, voided=False, reason_needs_credit_note=None, has_credit_note=False
            )
            novelty_id = await self.repository.create(
                line, kind, created, principal.email, self.clock()
            )
            novelty = await self.get(novelty_id)
        return await self._give_reason(principal, novelty, reason)

    # --- Credit notes ---

    async def attach_credit_note(
        self,
        principal: Principal,
        novelty_id: UUID,
        *,
        number: str | None,
        amount: Any,
        upload: UploadedFile | None,
    ) -> Novelty:
        """Register the supplier's credit note against a novelty: number, amount and its file.

        A credit note is one per supplier NIT and number: when the number was already
        registered for that supplier (it covers several lines) its file is reused and a new
        one is not needed (one sent is ignored).

        Raises:
            ForbiddenError: The caller does not manage the novelties.
            NotFoundError: No such novelty.
            ConflictError: It already has a credit note, or its entry was voided.
            InvalidInputError: Bad number, amount or file, or a new number without file.
        """

        await self.access.require_manage(
            principal, NOVELTIES, message="No tienes permiso para registrar notas crédito"
        )
        novelty = await self.get(novelty_id)
        if novelty.voided:
            raise ConflictError("La entrada de esta novedad fue anulada en SIESA")
        if novelty.credit_note is not None:
            raise ConflictError("La novedad ya tiene nota crédito; quítala para registrar otra")
        nit = (novelty.line.supplier_nit or "").strip()
        if not nit:
            raise InvalidInputError("La entrada no tiene el NIT del proveedor")
        number = clean_number(number)
        value = clean_amount(amount)
        existing = await self.repository.credit_note_by_number(nit, number)
        new_file: NewCreditNoteFile | None = None
        if existing is None:
            new_file = await self._store(nit, number, upload)
        status = derive_status(
            kind=novelty.kind,
            voided=False,
            reason_needs_credit_note=novelty.reason.requires_credit_note
            if novelty.reason
            else None,
            has_credit_note=True,
        )
        try:
            await self.repository.attach_credit_note(
                novelty.id,
                existing=existing,
                new_file=new_file,
                amount=value,
                status=status,
                actor=principal.email,
                now=self.clock(),
            )
        except Exception:
            if new_file is not None:
                await self._drop(new_file.storage_key)
            raise
        return await self.get(novelty.id)

    async def _store(self, nit: str, number: str, upload: UploadedFile | None) -> NewCreditNoteFile:
        if upload is None or not upload.content:
            raise InvalidInputError("Adjunta el soporte de la nota crédito (PDF o imagen)")
        reason = self.guard.refuse(upload.filename, len(upload.content), 0, 0)
        if reason:
            raise InvalidInputError(f"El soporte no se puede cargar: {reason}")
        name, extension, content_type, data = await self.guard.clean(
            upload.filename, upload.content
        )
        if extension not in CREDIT_NOTE_EXTENSIONS:
            raise InvalidInputError("El soporte debe ser un PDF o una imagen (PNG o JPG)")
        file_id = uuid4()
        key = f"costs/credit-notes/{file_id}.{extension}"
        await self.storage.put_object(key, data, content_type)
        return NewCreditNoteFile(
            id=file_id,
            supplier_nit=nit,
            number=number,
            original_filename=name,
            content_type=content_type,
            size_bytes=len(data),
            storage_key=key,
        )

    async def _drop(self, key: str) -> None:
        try:
            await self.storage.delete_object(key)
        except Exception:
            logger.warning("Could not remove credit note file %s", key)

    async def detach_credit_note(self, principal: Principal, novelty_id: UUID) -> Novelty:
        """Take the credit note off a novelty; its file goes when no other novelty uses it.

        Raises:
            ForbiddenError: The caller does not manage the novelties.
            NotFoundError: No such novelty, or it has no credit note.
        """

        await self.access.require_manage(
            principal, NOVELTIES, message="No tienes permiso para quitar notas crédito"
        )
        novelty = await self.get(novelty_id)
        if novelty.credit_note is None:
            raise NotFoundError("La novedad no tiene nota crédito")
        status = derive_status(
            kind=novelty.kind,
            voided=novelty.voided,
            reason_needs_credit_note=novelty.reason.requires_credit_note
            if novelty.reason
            else None,
            has_credit_note=False,
        )
        orphan = await self.repository.detach_credit_note(
            novelty.id, status, principal.email, self.clock()
        )
        if orphan is not None:
            await self._drop(orphan.storage_key)
        return await self.get(novelty.id)

    async def credit_note_file(self, novelty_id: UUID) -> tuple[CreditNote, bytes]:
        """The credit note file of a novelty.

        Raises:
            NotFoundError: No such novelty, or it has no credit note.
        """

        novelty = await self.get(novelty_id)
        if novelty.credit_note is None:
            raise NotFoundError("La novedad no tiene nota crédito")
        return novelty.credit_note, await self.storage.get_object(novelty.credit_note.storage_key)

    # --- The daily job ---

    def sync_window(self) -> tuple[date, date]:
        """The days the job reads: the last ``sync_days``, never before the start date."""

        until = self.today()
        since = max(self.start_date, until - timedelta(days=self.sync_days - 1))
        return since, until

    async def sync(self) -> Mapping[str, Any]:
        """Copy the novelties of the last days from the warehouse and refresh the kept ones.

        - A new line with a novelty is kept (``pendiente``); a line within the threshold is
          not kept.
        - A kept line is refreshed with the warehouse's copy: a quotation dated back may turn
          it into no novelty (it resolves itself if it had no reason yet).
        - A kept line the warehouse no longer has (its document was voided) is marked
          ``anulada``, never deleted; it comes back if the line does.

        Returns:
            Mapping[str, Any]: What the run did (:class:`SyncReport`).
        """

        since, until = self.sync_window()
        if since > until:
            return SyncReport(since, until, 0, 0, 0, 0, 0, 0).as_dict()
        window = await self.source.lines(since, until)
        lines: list[EntryLine] = with_status(window.lines, self.threshold)
        if not window.previous_from_warehouse:
            lines = fill_previous_prices(lines)
        states = {
            state.line_key: state for state in await self.repository.states_between(since, until)
        }
        seen = {line.line_key for line in lines}
        created: list[tuple[EntryLine, str, str]] = []
        changed: list[SyncChange] = []
        novelties_seen = 0
        for line in lines:
            kind = kind_of(line.status)
            if kind != NO_LONGER:
                novelties_seen += 1
            state = states.get(line.line_key)
            if state is None:
                if kind == NO_LONGER:
                    continue
                status = derive_status(
                    kind=kind, voided=False, reason_needs_credit_note=None, has_credit_note=False
                )
                created.append((line, kind, status))
                continue
            event = EVENT_RESTORED if state.voided else EVENT_REREAD if kind != state.kind else None
            changed.append(
                SyncChange(
                    state.id, line, kind, False, state.derived(kind=kind, voided=False), event
                )
            )
        voided = 0
        # An empty read is a warehouse being rebuilt, not every document voided at once.
        if lines:
            for key, state in states.items():
                if key not in seen and not state.voided:
                    voided += 1
                    changed.append(
                        SyncChange(
                            state.id,
                            None,
                            state.kind,
                            True,
                            state.derived(voided=True),
                            EVENT_VOIDED,
                        )
                    )
        await self.repository.apply_sync(created, changed, self.clock())
        restored = sum(1 for change in changed if change.event == EVENT_RESTORED)
        refreshed = sum(1 for change in changed if change.line is not None)
        return SyncReport(
            since=since,
            until=until,
            lines_read=len(lines),
            novelties_seen=novelties_seen,
            created=len(created),
            refreshed=refreshed,
            voided=voided,
            restored=restored,
        ).as_dict()


__all__ = ["MAX_PAGE_SIZE", "NoveltyService", "UploadedFile", "clean_amount", "clean_number"]
