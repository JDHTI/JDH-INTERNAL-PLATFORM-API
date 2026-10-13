"""Use cases of the retained orders validator."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.core.errors import ForbiddenError, InvalidInputError, NotFoundError
from app.modules.identity.domain.principal import Principal
from app.modules.order_validation.domain import rules, vocabulary
from app.modules.order_validation.domain.entities import (
    Batch,
    BatchPage,
    LineResult,
    OrderLine,
    OrderPage,
    OrderView,
    StoredLine,
)
from app.modules.order_validation.domain.ports import (
    AgreementSource,
    BatchRepository,
    Calendar,
    ManagerPolicy,
    OrderFileReader,
)

MAX_PAGE_SIZE = 200


def _matches(order: OrderView, term: str) -> bool:
    """Free text over the document, the client and the supplier and SKU of any line."""

    for item in order.lines:
        line = item.line
        for value in (
            line.document,
            line.customer_nit,
            line.customer_name,
            line.supplier_code,
            line.supplier_name,
            line.sku,
            line.description,
            line.salesperson,
        ):
            if value and term in value.lower():
                return True
    return False


def _total(lines: Sequence[StoredLine]) -> Decimal | None:
    amounts = [
        item.line.unit_price * item.line.quantity
        for item in lines
        if item.line.unit_price is not None and item.line.quantity is not None
    ]
    return sum(amounts, Decimal(0)) if amounts else None


class OrderValidationService:
    """Upload retained orders files and check every line against the supports master."""

    def __init__(
        self,
        repository: BatchRepository,
        agreements: AgreementSource,
        reader: OrderFileReader,
        managers: ManagerPolicy,
        calendar: Calendar,
        max_upload_bytes: int,
        max_lines: int,
    ) -> None:
        """Create the service.

        Args:
            repository (BatchRepository): Persistence of files, lines and results.
            agreements (AgreementSource): Supports, list margins and clients.
            reader (OrderFileReader): Reads the uploaded spreadsheet.
            managers (ManagerPolicy): Decides who may upload and re-validate.
            calendar (Calendar): Business-day clock, for lines with no order date.
            max_upload_bytes (int): Largest file accepted.
            max_lines (int): Most lines accepted per file.
        """

        self.repository = repository
        self.agreements = agreements
        self.reader = reader
        self.managers = managers
        self.calendar = calendar
        self.max_upload_bytes = max_upload_bytes
        self.max_lines = max_lines

    async def _require_manager(self, principal: Principal) -> None:
        if not await self.managers.is_manager(principal):
            raise ForbiddenError("No tienes permiso para validar pedidos retenidos")

    async def can_manage(self, principal: Principal) -> bool:
        """Whether the caller may upload files and re-run validations (for UI gating)."""

        return await self.managers.is_manager(principal)

    # ------------------------------------------------------------ validation

    async def _validate(self, lines: Sequence[OrderLine]) -> list[LineResult]:
        """Decide every line against the supports in force on its order date."""

        codes = sorted({line.supplier_code for line in lines if line.supplier_code})
        skus = sorted({line.sku for line in lines if line.sku})
        nits = sorted({line.customer_nit for line in lines if line.customer_nit})
        grouped = rules.by_supplier(await self.agreements.agreements(codes, skus, nits))
        margins = await self.agreements.supplier_margins(codes)
        customers = await self.agreements.customers(nits)
        today = self.calendar.today()
        return [
            rules.decide(
                line,
                [*grouped.get(line.supplier_code, []), *grouped.get(None, [])],
                customers.get(line.customer_nit or ""),
                margins.get(line.supplier_code or ""),
                today,
            )
            for line in lines
        ]

    async def upload(self, principal: Principal, filename: str | None, content: bytes) -> Batch:
        """Read a retained orders file, validate every line and keep the result.

        Args:
            principal (Principal): The caller; must be a manager.
            filename (str | None): Name of the uploaded file (``.xlsx`` or ``.csv``).
            content (bytes): The file; one byte over the limit is enough to reject it.

        Returns:
            Batch: The stored file with its counts.

        Raises:
            ForbiddenError: If the caller is not a manager.
            InvalidInputError: If the file is empty, too big, not a spreadsheet, lacks the
                required columns or has too many lines.
        """

        await self._require_manager(principal)
        name = (filename or "").strip()
        if not name:
            raise InvalidInputError("El archivo no tiene nombre")
        if not content:
            raise InvalidInputError("El archivo está vacío")
        if len(content) > self.max_upload_bytes:
            limit = self.max_upload_bytes // (1024 * 1024)
            raise InvalidInputError(f"El archivo supera el máximo de {limit} MB")
        lines = self.reader.read(name, content)
        if not lines:
            raise InvalidInputError("El archivo no tiene líneas de pedido")
        if len(lines) > self.max_lines:
            raise InvalidInputError(f"El archivo supera el máximo de {self.max_lines} líneas")
        results = await self._validate(lines)
        return await self.repository.create(
            uuid4(),
            name[:255],
            principal.email,
            datetime.now(UTC),
            list(zip(lines, results, strict=True)),
        )

    async def revalidate(self, principal: Principal, batch_id: UUID) -> Batch:
        """Check a stored file again against the supports as they are now.

        Raises:
            ForbiddenError: If the caller is not a manager.
            NotFoundError: If the file does not exist.
        """

        await self._require_manager(principal)
        if await self.repository.get(batch_id) is None:
            raise NotFoundError("Validación no encontrada")
        stored = await self.repository.lines(batch_id)
        results = await self._validate([item.line for item in stored])
        return await self.repository.save_results(
            batch_id,
            [(item.id, result) for item, result in zip(stored, results, strict=True)],
            principal.email,
            datetime.now(UTC),
        )

    # --------------------------------------------------------------- queries

    async def list_batches(self, page: int = 1, size: int = 20) -> BatchPage:
        """Uploaded files, newest first."""

        self._bounds(page, size)
        return await self.repository.list(page, size)

    async def get_batch(self, batch_id: UUID) -> Batch:
        """One uploaded file with its counts."""

        batch = await self.repository.get(batch_id)
        if batch is None:
            raise NotFoundError("Validación no encontrada")
        return batch

    async def orders(
        self,
        batch_id: UUID,
        *,
        status: str | None = None,
        reason: str | None = None,
        q: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> OrderPage:
        """The orders of a file, each with its lines, in file order.

        Args:
            batch_id (UUID): The uploaded file.
            status (str | None): Only orders ready for release or pending.
            reason (str | None): Only orders with at least one line for this reason.
            q (str | None): Free text over document, client, supplier and SKU.
            page (int): 1-based page.
            size (int): Page size, up to :data:`MAX_PAGE_SIZE`.

        Returns:
            OrderPage: The page, plus the order count per status before filtering by it.
        """

        self._bounds(page, size)
        if status is not None and status not in vocabulary.STATUSES:
            raise InvalidInputError(f"status debe ser uno de: {', '.join(vocabulary.STATUSES)}")
        if reason is not None and reason not in vocabulary.REASONS:
            raise InvalidInputError(f"reason debe ser uno de: {', '.join(vocabulary.REASONS)}")
        await self.get_batch(batch_id)

        documents: dict[str, list[StoredLine]] = {}
        for item in await self.repository.lines(batch_id):
            documents.setdefault(item.line.document, []).append(item)
        orders = [
            OrderView(
                document=document,
                status=rules.order_status(item.result.status for item in lines),
                lines=lines,
                total_amount=_total(lines),
            )
            for document, lines in documents.items()
        ]
        term = " ".join((q or "").lower().split())
        if term:
            orders = [order for order in orders if _matches(order, term)]
        if reason:
            orders = [
                order
                for order in orders
                if any(item.result.reason == reason for item in order.lines)
            ]
        counts = {value: 0 for value in vocabulary.STATUSES}
        for order in orders:
            counts[order.status] += 1
        if status:
            orders = [order for order in orders if order.status == status]
        start = (page - 1) * size
        return OrderPage(
            items=orders[start : start + size],
            total=len(orders),
            page=page,
            size=size,
            counts=counts,
        )

    @staticmethod
    def catalogs() -> dict[str, list[dict[str, str]]]:
        """Statuses and reasons with the labels the screens show."""

        return {
            "statuses": [
                {"value": value, "label": vocabulary.STATUS_LABELS[value]}
                for value in vocabulary.STATUSES
            ],
            "reasons": [
                {
                    "value": value,
                    "label": vocabulary.REASON_LABELS[value],
                    "status": (
                        vocabulary.READY
                        if value in vocabulary.READY_REASONS
                        else vocabulary.PENDING
                    ),
                }
                for value in vocabulary.REASONS
            ],
        }

    @staticmethod
    def _bounds(page: int, size: int) -> None:
        if page < 1:
            raise InvalidInputError("La página debe ser mayor o igual a 1")
        if not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"El tamaño debe estar entre 1 y {MAX_PAGE_SIZE}")


__all__ = ["MAX_PAGE_SIZE", "OrderValidationService"]
