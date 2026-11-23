"""The price increases the suppliers send: the «MAESTRA» workbook, the manual form and the
review that moves them into the history of increases."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.modules.costs.application.access import CostAccess
from app.modules.costs.domain.entries import supplier_name
from app.modules.costs.domain.increases import (
    APPROVED,
    FROM_FORM,
    FROM_TEMPLATE,
    IN_REVIEW,
    MAX_LINES,
    MAX_NOTE,
    REGIONAL_NAMES,
    REJECTED,
    REQUEST_STATUSES,
    IncreaseRequest,
    IncreaseRequestPage,
    LineInput,
    RequestLine,
    RowIssue,
    SupplierItem,
    UploadReport,
    build_line,
    list_price,
)
from app.modules.costs.domain.modules import INCREASES
from app.modules.costs.domain.ports import (
    IncreaseLedger,
    IncreaseRepository,
    LedgerEntry,
    ObjectStorage,
    SupplierItemSource,
    WorkbookReader,
)
from app.modules.identity.domain.principal import Principal

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 100
MAX_REPORTED_ISSUES = 500
_BOGOTA = ZoneInfo("America/Bogota")
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass(frozen=True)
class LineChanges:
    """What a reviewer may correct on a line; ``None`` leaves it as it is."""

    accepted: bool | None = None
    new_cost: Decimal | None = None
    increased_on: date | None = None
    regular_discount_pct: Decimal | None = None
    regional_discounts: dict[str, Decimal] | None = None
    observation: str | None = None


def _clean_note(note: str | None) -> str | None:
    text = " ".join((note or "").split())
    if len(text) > MAX_NOTE:
        raise InvalidInputError(f"La nota pasa de {MAX_NOTE} caracteres")
    return text or None


def _check_values(line: LineInput) -> list[str]:
    problems: list[str] = []
    if line.new_cost is None or line.new_cost <= 0:
        problems.append("El costo nuevo debe ser mayor que cero")
    if line.increased_on is None:
        problems.append("Falta la fecha de incremento")
    for label, value in (
        ("El ICUI", line.icui_pct),
        ("El descuento regular", line.regular_discount_pct),
    ):
        if value is not None and not 0 <= value <= 1:
            problems.append(f"{label} debe estar entre 0 % y 100 %")
    if line.ibua is not None and line.ibua < 0:
        problems.append("El IBUA no puede ser negativo")
    for name, value in line.regional_discounts.items():
        if name not in REGIONAL_NAMES:
            problems.append(f"Regional desconocida: {name}")
        elif not 0 <= value <= 1:
            problems.append(f"El descuento de {name} debe estar entre 0 % y 100 %")
    return problems


class IncreaseService:
    """Supplier increase requests (``alzas-proveedor``)."""

    def __init__(
        self,
        repository: IncreaseRepository,
        items: SupplierItemSource,
        ledger: IncreaseLedger,
        access: CostAccess,
        storage: ObjectStorage,
        reader: WorkbookReader,
        writer: Callable[[Sequence[SupplierItem], Sequence[str], date], bytes],
        namer: Callable[[str, str | None, date], str],
        reasons: Callable[[], Any],
        max_upload_bytes: int,
        today: Callable[[], date] = lambda: datetime.now(_BOGOTA).date(),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create the service.

        Args:
            repository (IncreaseRepository): The requests and their lines.
            items (SupplierItemSource): The suppliers' active products (the warehouse).
            ledger (IncreaseLedger): The history of increases, where approved lines go.
            access (CostAccess): Who reads and manages each Costos key.
            storage (ObjectStorage): Where the returned workbooks are kept.
            reader (WorkbookReader): Reads a returned «MAESTRA».
            writer (Callable): Writes a supplier's «MAESTRA».
            namer (Callable): Names it (``MAESTRA 50 - ASCENDER S.A. 23-11-2026.xlsx``).
            reasons (Callable): The reason names for the hidden ``Motivos`` sheet (awaitable).
            max_upload_bytes (int): Largest workbook accepted.
            today (Callable[[], date]): Today in Bogotá.
            clock (Callable[[], datetime]): Now, in UTC.
        """

        self.repository = repository
        self.items_source = items
        self.ledger = ledger
        self.access = access
        self.storage = storage
        self.reader = reader
        self.writer = writer
        self.namer = namer
        self.reasons = reasons
        self.max_upload_bytes = max_upload_bytes
        self.today = today
        self.clock = clock

    async def _require_manager(self, principal: Principal) -> None:
        await self.access.require_manage(
            principal, INCREASES, message="No tienes permiso para gestionar las alzas de proveedor"
        )

    async def _items(self, supplier_code: str) -> list[SupplierItem]:
        code = (supplier_code or "").strip()
        if not code or len(code) > 20:
            raise InvalidInputError("Elige el proveedor")
        items = await self.items_source.items(code)
        if not items:
            raise NotFoundError("El proveedor no tiene productos activos en SIESA")
        return items

    # --- Template and items ---

    async def items(self, supplier_code: str, q: str | None = None) -> list[SupplierItem]:
        """The supplier's active products for the manual form, narrowed by reference prefix or
        description."""

        items = await self._items(supplier_code)
        term = (q or "").strip().lower()
        if not term:
            return items
        return [
            item
            for item in items
            if item.sku.startswith(term) or term in (item.description or "").lower()
        ]

    async def template(self, supplier_code: str) -> tuple[str, bytes]:
        """The supplier's «MAESTRA» workbook and its name.

        Raises:
            InvalidInputError: No supplier.
            NotFoundError: The supplier has no active products.
            DependencyUnavailableError: The warehouse did not answer.
        """

        items = await self._items(supplier_code)
        # As the area's workbook: the products the supplier quotes today. One without a
        # quotation may still come back typed in a new row, or by the form.
        quoted = [item for item in items if item.old_cost is not None] or items
        day = self.today()
        reasons = [reason.name for reason in await self.reasons()]
        content = await asyncio.to_thread(self.writer, quoted, reasons, day)
        name = supplier_name(items[0].supplier_label)
        return self.namer(supplier_code.strip(), name, day), content

    # --- Creating requests ---

    def _lines(
        self, items: Sequence[SupplierItem], inputs: Sequence[LineInput]
    ) -> tuple[list[RequestLine], list[RowIssue]]:
        by_sku = {item.sku: item for item in items}
        lines: list[RequestLine] = []
        issues: list[RowIssue] = []
        seen: set[str] = set()
        for position, values in enumerate(inputs, start=1):
            row = values.row_number or position
            problems = _check_values(values)
            item = by_sku.get(values.sku)
            if item is None:
                problems.append("La referencia no es un producto activo del proveedor")
            if values.sku in seen:
                problems.append("La referencia está repetida")
            seen.add(values.sku)
            if problems:
                issues.extend(RowIssue(row, values.sku, problem) for problem in problems)
                continue
            lines.append(build_line(uuid4(), item, values))
        return lines, issues

    def _request(
        self,
        principal: Principal,
        supplier_code: str,
        items: Sequence[SupplierItem],
        source: str,
        lines: Sequence[RequestLine],
        note: str | None,
        file_name: str | None = None,
        storage_key: str | None = None,
        request_id: UUID | None = None,
    ) -> IncreaseRequest:
        now = self.clock()
        return IncreaseRequest(
            id=request_id or uuid4(),
            supplier_code=supplier_code,
            supplier_name=supplier_name(items[0].supplier_label),
            source=source,
            status=IN_REVIEW,
            file_name=file_name,
            storage_key=storage_key,
            note=note,
            created_by=principal.email,
            created_at=now,
            decided_by=None,
            decided_at=None,
            decision_note=None,
            line_count=len(lines),
            accepted_count=sum(1 for line in lines if line.accepted),
            lines=tuple(lines),
        )

    async def upload(
        self,
        principal: Principal,
        supplier_code: str,
        filename: str | None,
        content: bytes,
        *,
        apply: bool,
        note: str | None = None,
    ) -> UploadReport:
        """Read a returned «MAESTRA»: the rows with a new cost become a request in review.

        With ``apply`` false nothing is stored (a simulation). One problem blocks the load.

        Raises:
            ForbiddenError: The caller does not manage the increases.
            InvalidInputError: Empty, too large or unreadable workbook, or no new cost in it.
            NotFoundError: The supplier has no active products.
        """

        await self._require_manager(principal)
        note = _clean_note(note)
        if not content:
            raise InvalidInputError("El archivo está vacío")
        if len(content) > self.max_upload_bytes:
            raise InvalidInputError(
                f"El archivo pasa de {self.max_upload_bytes // (1024 * 1024)} MB"
            )
        code = (supplier_code or "").strip()
        items = await self._items(code)
        read = await asyncio.to_thread(self.reader.read, filename, content)
        if not read.lines and not read.issues:
            raise InvalidInputError("El archivo no trae ningún costo nuevo")
        if len(read.lines) > MAX_LINES:
            raise InvalidInputError(f"El archivo pasa de {MAX_LINES} referencias con costo nuevo")
        lines, issues = self._lines(items, read.lines)
        issues = [*read.issues, *issues]
        issues.sort(key=lambda issue: issue.row_number)
        request_id = None
        if apply and not issues:
            request_id = uuid4()
            key = f"costs/increase-requests/{request_id}.xlsx"
            await self.storage.put_object(key, content, _XLSX)
            request = self._request(
                principal,
                code,
                items,
                FROM_TEMPLATE,
                lines,
                note,
                filename or "maestra.xlsx",
                key,
                request_id,
            )
            try:
                await self.repository.create(request)
            except Exception:
                await self._drop(key)
                raise
        return UploadReport(
            applied=request_id is not None,
            supplier_code=code,
            rows_read=read.rows_read,
            lines=tuple(lines),
            issues=tuple(issues[:MAX_REPORTED_ISSUES]),
            request_id=request_id,
        )

    async def create(
        self,
        principal: Principal,
        supplier_code: str,
        inputs: Sequence[LineInput],
        note: str | None = None,
    ) -> IncreaseRequest:
        """A request typed by hand: a few references of one supplier.

        Raises:
            ForbiddenError: The caller does not manage the increases.
            InvalidInputError: No lines, too many, or a line with problems (all listed).
            NotFoundError: The supplier has no active products.
        """

        await self._require_manager(principal)
        note = _clean_note(note)
        if not inputs:
            raise InvalidInputError("Agrega al menos una referencia")
        if len(inputs) > MAX_LINES:
            raise InvalidInputError(f"Máximo {MAX_LINES} referencias por solicitud")
        code = (supplier_code or "").strip()
        items = await self._items(code)
        lines, issues = self._lines(items, inputs)
        if issues:
            detail = "; ".join(f"{issue.sku or '-'}: {issue.problem}" for issue in issues[:20])
            raise InvalidInputError(f"Hay referencias con problemas: {detail}")
        request = self._request(principal, code, items, FROM_FORM, lines, note)
        await self.repository.create(request)
        return await self.get(request.id)

    # --- Reading ---

    async def page(
        self, status: str | None, supplier: str | None, page: int, size: int
    ) -> IncreaseRequestPage:
        """The requests, the latest first, with the counts by status.

        Raises:
            InvalidInputError: Unknown status or bad page.
        """

        if page < 1 or not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"Página inválida; el tamaño máximo es {MAX_PAGE_SIZE}")
        if status is not None and status not in REQUEST_STATUSES:
            raise InvalidInputError("Estado desconocido: en_revision, aprobada o rechazada")
        return await self.repository.page(status, (supplier or "").strip() or None, page, size)

    async def get(self, request_id: UUID) -> IncreaseRequest:
        """A request with its lines.

        Raises:
            NotFoundError: No such request.
        """

        request = await self.repository.get(request_id, with_lines=True)
        if request is None:
            raise NotFoundError("Solicitud no encontrada")
        return request

    async def file(self, request_id: UUID) -> tuple[str, bytes]:
        """The workbook the supplier returned.

        Raises:
            NotFoundError: No such request, or it was typed by hand.
        """

        request = await self.repository.get(request_id, with_lines=False)
        if request is None or not request.storage_key:
            raise NotFoundError("La solicitud no tiene archivo")
        return request.file_name or "maestra.xlsx", await self.storage.get_object(
            request.storage_key
        )

    # --- Review ---

    async def _open(self, principal: Principal, request_id: UUID) -> IncreaseRequest:
        await self._require_manager(principal)
        request = await self.get(request_id)
        if request.status != IN_REVIEW:
            raise ConflictError("La solicitud ya fue decidida")
        return request

    async def update_line(
        self, principal: Principal, request_id: UUID, line_id: UUID, changes: LineChanges
    ) -> IncreaseRequest:
        """Correct a line in review, or leave it out (``accepted`` false).

        Raises:
            ForbiddenError: The caller does not manage the increases.
            NotFoundError: No such request or line.
            ConflictError: The request was decided.
            InvalidInputError: A value out of range.
        """

        request = await self._open(principal, request_id)
        line = next((item for item in request.lines if item.id == line_id), None)
        if line is None:
            raise NotFoundError("Línea no encontrada")
        observation = line.observation
        if changes.observation is not None:
            observation = " ".join(changes.observation.split()).upper()[:MAX_NOTE] or None
        values = LineInput(
            sku=line.sku,
            new_cost=changes.new_cost if changes.new_cost is not None else line.new_cost,
            increased_on=changes.increased_on
            if changes.increased_on is not None
            else line.increased_on,
            supplier_sku=line.supplier_sku,
            ean=line.ean,
            icui_pct=line.icui_pct,
            ibua=line.ibua,
            regular_discount_pct=(
                changes.regular_discount_pct
                if changes.regular_discount_pct is not None
                else line.regular_discount_pct
            ),
            regional_discounts=(
                changes.regional_discounts
                if changes.regional_discounts is not None
                else line.regional_discounts
            ),
            observation=observation,
            accepted=changes.accepted if changes.accepted is not None else line.accepted,
            row_number=line.row_number,
        )
        problems = _check_values(values)
        if problems:
            raise InvalidInputError("; ".join(problems))
        item = SupplierItem(
            sku=line.sku,
            supplier_sku=line.supplier_sku,
            ean=line.ean,
            nit=None,
            supplier_label=None,
            description=line.description,
            pack_factor=line.pack_factor,
            state="Activo",
            category=line.category,
            tax_group=line.tax_group,
            icui_pct=line.icui_pct,
            ibua=line.ibua,
            old_cost=line.old_cost,
            old_cost_with_tax=line.old_cost_with_tax,
        )
        await self.repository.update_line(
            request.id, build_line(line.id, item, values), self.clock()
        )
        return await self.get(request.id)

    async def approve(
        self, principal: Principal, request_id: UUID, note: str | None = None
    ) -> IncreaseRequest:
        """Approve a request: each accepted line becomes a row of the history of increases,
        without a SIESA date (the quoter takes it as pending), with the «Lista 1» margin and
        its price.

        Raises:
            ForbiddenError: The caller does not manage the increases.
            NotFoundError: No such request.
            ConflictError: Already decided.
            InvalidInputError: No accepted line, or one without increase date.
        """

        request = await self._open(principal, request_id)
        note = _clean_note(note)
        accepted = [line for line in request.lines if line.accepted]
        if not accepted:
            raise InvalidInputError("No hay líneas aceptadas para aprobar; rechaza la solicitud")
        undated = [line.sku for line in accepted if line.increased_on is None]
        if undated:
            raise InvalidInputError(f"Falta la fecha de incremento en: {', '.join(undated[:20])}")
        margins = await self.ledger.margins([line.sku for line in accepted])
        notified = request.created_at.astimezone(_BOGOTA).date()
        entries = []
        for line in accepted:
            margin = margins.get(line.sku)
            entries.append(
                LedgerEntry(
                    line_id=line.id,
                    sku=line.sku,
                    notified_on=notified,
                    increased_on=line.increased_on,
                    supplier_sku=line.supplier_sku,
                    ean=line.ean,
                    icui_pct=line.icui_pct,
                    ibua=line.ibua,
                    old_cost=line.old_cost,
                    old_cost_with_tax=line.old_cost_with_tax,
                    new_cost=line.new_cost,
                    new_cost_with_tax=line.new_cost_with_tax,
                    variation_amount=line.variation_amount,
                    variation_pct=line.variation_pct,
                    status=line.change,
                    observations=self._observations(line),
                    margin_pct=margin,
                    price=list_price(line.new_cost_with_tax, margin),
                )
            )
        now = self.clock()
        ids = await self.ledger.record(entries, principal.email, now)
        await self.repository.decide(request.id, APPROVED, principal.email, now, note, ids)
        return await self.get(request.id)

    @staticmethod
    def _observations(line: RequestLine) -> str | None:
        parts = []
        if line.observation:
            parts.append(line.observation)
        if line.regular_discount_pct is not None:
            parts.append(f"Descuento regular {line.regular_discount_pct * 100:.2f} %")
        for name, value in sorted(line.regional_discounts.items()):
            parts.append(f"Descuento {name} {value * 100:.2f} %")
        return " · ".join(parts) or None

    async def reject(
        self, principal: Principal, request_id: UUID, note: str | None
    ) -> IncreaseRequest:
        """Reject a request; it says why.

        Raises:
            ForbiddenError: The caller does not manage the increases.
            NotFoundError: No such request.
            ConflictError: Already decided.
            InvalidInputError: No reason given.
        """

        request = await self._open(principal, request_id)
        note = _clean_note(note)
        if not note:
            raise InvalidInputError("Escribe por qué se rechaza")
        await self.repository.decide(request.id, REJECTED, principal.email, self.clock(), note, {})
        return await self.get(request.id)

    async def _drop(self, key: str) -> None:
        try:
            await self.storage.delete_object(key)
        except Exception:
            logger.warning("Could not remove increase workbook %s", key)


__all__ = ["MAX_PAGE_SIZE", "IncreaseService", "LineChanges"]
