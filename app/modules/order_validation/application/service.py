"""Use cases of the retained orders validator."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.identity.domain.principal import Principal
from app.modules.order_validation.domain import rules, vocabulary
from app.modules.order_validation.domain.catalog import Section, catalog
from app.modules.order_validation.domain.entities import (
    Batch,
    BatchPage,
    LineResult,
    ManualApproval,
    OrderLine,
    OrderPage,
    OrderPolicy,
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
MAX_NOTE_LENGTH = 500
MAX_APPROVALS = 1000
MAX_THRESHOLD_POINTS = Decimal(20)
SORTS = ("attention", "file")


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


def _order_key(order: OrderView) -> tuple[int, int, Decimal, int]:
    """Orders with a line that has no support first, then margin from lowest to highest."""

    margin = order.margin_pct if order.margin_pct is not None else Decimal("Infinity")
    return (
        0 if any(item.result.reason == "sin_acuerdo" for item in order.lines) else 1,
        0 if order.margin_pct is not None else 1,
        margin,
        order.first.row_number,
    )


def _attention_lines(order: OrderView) -> OrderView:
    """The same order with its lines in attention order (no support first, lowest margin)."""

    return replace(
        order,
        lines=sorted(order.lines, key=lambda item: rules.line_sort_key(item.line, item.result)),
    )


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
        policy: OrderPolicy = rules.DEFAULT_POLICY,
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
            policy (OrderPolicy): Order-level limits (tolerance, points below target). The
                repository must count with the same one.
        """

        self.repository = repository
        self.agreements = agreements
        self.reader = reader
        self.managers = managers
        self.calendar = calendar
        self.max_upload_bytes = max_upload_bytes
        self.max_lines = max_lines
        self.policy = policy

    async def _require_manager(self, principal: Principal) -> None:
        if not await self.managers.is_manager(principal):
            raise ForbiddenError("No tienes permiso para validar pedidos retenidos")

    async def can_manage(self, principal: Principal) -> bool:
        """Whether the caller may upload files and re-run validations (for UI gating)."""

        return await self.managers.is_manager(principal)

    # ------------------------------------------------------------ validation

    @staticmethod
    def _threshold(points: Decimal | None) -> Decimal | None:
        """Margin points as the fraction the rules use; `None` when not given."""

        if points is None:
            return None
        if not Decimal(0) <= points <= MAX_THRESHOLD_POINTS:
            raise InvalidInputError(
                f"El umbral debe estar entre 0 y {MAX_THRESHOLD_POINTS} puntos de margen"
            )
        return (points / 100).quantize(Decimal("0.000001"))

    async def _validate(self, lines: Sequence[OrderLine]) -> list[LineResult]:
        """Decide every line against the supports in force on its order date."""

        codes = sorted({line.supplier_code for line in lines if line.supplier_code})
        skus = sorted({line.sku for line in lines if line.sku})
        nits = sorted({line.customer_nit for line in lines if line.customer_nit})
        grouped = rules.by_supplier(await self.agreements.agreements(codes, skus, nits))
        products = await self.agreements.products(skus)
        customers = await self.agreements.customers(nits)
        today = self.calendar.today()
        return [
            rules.decide(
                line,
                [*grouped.get(line.supplier_code, []), *grouped.get(None, [])],
                customers.get(line.customer_nit or ""),
                products.get(line.sku or ""),
                today,
            )
            for line in lines
        ]

    async def upload(
        self,
        principal: Principal,
        filename: str | None,
        content: bytes,
        threshold_points: Decimal | None = None,
    ) -> Batch:
        """Read a retained orders file, validate every line and keep the result.

        Args:
            principal (Principal): The caller; must be a manager.
            filename (str | None): Name of the uploaded file (``.xlsx`` or ``.csv``).
            content (bytes): The file; one byte over the limit is enough to reject it.
            threshold_points (Decimal | None): Margin points an order may fall short by and still
                be released (0.5 releases an order at 19,5 % against a 20 % target).

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
        threshold = self._threshold(threshold_points) or Decimal(0)
        results = await self._validate(lines)
        return await self.repository.create(
            uuid4(),
            name[:255],
            principal.email,
            datetime.now(UTC),
            list(zip(lines, results, strict=True)),
            threshold,
        )

    async def revalidate(
        self,
        principal: Principal,
        batch_id: UUID,
        threshold_points: Decimal | None = None,
    ) -> Batch:
        """Check a stored file again against the supports and margins as they are now.

        Args:
            principal (Principal): The caller; must be a manager.
            batch_id (UUID): The stored file.
            threshold_points (Decimal | None): New margin threshold in points; the stored one
                is kept when omitted. Manual approvals are kept either way.

        Raises:
            ForbiddenError: If the caller is not a manager.
            NotFoundError: If the file does not exist.
            InvalidInputError: If the threshold is out of range.
        """

        await self._require_manager(principal)
        batch = await self.repository.get(batch_id)
        if batch is None:
            raise NotFoundError("Validación no encontrada")
        given = self._threshold(threshold_points)
        threshold = batch.margin_threshold if given is None else given
        stored = await self.repository.lines(batch_id)
        results = await self._validate([item.line for item in stored])
        return await self.repository.save_results(
            batch_id,
            [(item.id, result) for item, result in zip(stored, results, strict=True)],
            principal.email,
            datetime.now(UTC),
            threshold,
        )

    async def set_threshold(
        self, principal: Principal, batch_id: UUID, threshold_points: Decimal
    ) -> Batch:
        """Save the margin threshold of a file and recount its orders; lines are not re-checked.

        The threshold acts on the margin of each order, so changing it does not change any line:
        only which orders are released. Manual approvals are kept.

        Raises:
            ForbiddenError: If the caller is not a manager.
            NotFoundError: If the file does not exist.
            InvalidInputError: If the threshold is out of range.
        """

        await self._require_manager(principal)
        threshold = self._threshold(threshold_points) or Decimal(0)
        batch = await self.repository.set_threshold(batch_id, threshold)
        if batch is None:
            raise NotFoundError("Validación no encontrada")
        return batch

    async def approve(
        self,
        principal: Principal,
        batch_id: UUID,
        documents: Sequence[str],
        note: str | None = None,
    ) -> Batch:
        """Mark orders as released, pending or ready alike, all or nothing.

        The mark belongs to the order: its lines keep their own result, and
        re-validating the file keeps the mark.

        Raises:
            ForbiddenError: If the caller is not a manager.
            NotFoundError: If the file does not exist or a document is not in it.
            ConflictError: If one of the orders is already released.
            InvalidInputError: If no document is sent or the note is too long.
        """

        await self._require_manager(principal)
        wanted = list(dict.fromkeys(item.strip() for item in documents if item and item.strip()))
        if not wanted:
            raise InvalidInputError("Indica al menos un pedido para marcar como liberado")
        text = " ".join((note or "").split()) or None
        if text and len(text) > MAX_NOTE_LENGTH:
            raise InvalidInputError(f"La nota admite máximo {MAX_NOTE_LENGTH} caracteres")
        batch = await self.get_batch(batch_id)
        orders = {
            order.document: order
            for order in await self._orders_of(batch_id, batch.margin_threshold)
        }
        missing = [document for document in wanted if document not in orders]
        if missing:
            raise NotFoundError(f"Pedidos que no están en el archivo: {', '.join(missing)}")
        settled = [
            document for document in wanted if orders[document].status == vocabulary.RELEASED
        ]
        if settled:
            raise ConflictError(
                f"Estos pedidos ya están liberados: {', '.join(settled)}; no se marcó ninguno"
            )
        now = datetime.now(UTC)
        return await self.repository.approve(
            batch_id,
            [ManualApproval(document, principal.email, now, text) for document in wanted],
        )

    async def remove_approval(self, principal: Principal, batch_id: UUID, document: str) -> None:
        """Undo a release mark; the order goes back to what the validation says.

        Raises:
            ForbiddenError: If the caller is not a manager.
            NotFoundError: If the order is not marked as released.
        """

        await self._require_manager(principal)
        if not await self.repository.remove_approval(batch_id, document.strip()):
            raise NotFoundError("El pedido no está marcado como liberado")

    async def delete(self, principal: Principal, batch_id: UUID) -> None:
        """Delete a file with its lines and approvals.

        Raises:
            ForbiddenError: If the caller is not a manager.
            NotFoundError: If the file does not exist.
        """

        await self._require_manager(principal)
        if not await self.repository.delete(batch_id):
            raise NotFoundError("Validación no encontrada")

    # --------------------------------------------------------------- queries

    async def _orders_of(self, batch_id: UUID, threshold: Decimal) -> list[OrderView]:
        """Every order of a file in file order.

        An order a person marked is released (`release_basis` is `manual`). Otherwise it is
        ready when all its lines are or when the margin `threshold` releases it.
        """

        approvals = await self.repository.approvals(batch_id)
        documents: dict[str, list[StoredLine]] = {}
        for item in await self.repository.lines(batch_id):
            documents.setdefault(item.line.document, []).append(item)
        orders = []
        for document, lines in documents.items():
            pairs = [(item.line, item.result) for item in lines]
            margin, required = rules.order_margins(pairs)
            basis = rules.release_basis(pairs, document in approvals, threshold, self.policy)
            orders.append(
                OrderView(
                    document=document,
                    status=(
                        vocabulary.RELEASED
                        if document in approvals
                        else vocabulary.READY
                        if basis
                        else vocabulary.PENDING
                    ),
                    lines=lines,
                    total_amount=_total(lines),
                    manual_approval=approvals.get(document),
                    margin_pct=margin,
                    required_margin_pct=required,
                    margin_with_threshold_pct=(
                        margin + threshold if margin is not None and threshold > 0 else None
                    ),
                    release_basis=basis,
                    released_by_threshold=(
                        basis == rules.RELEASE_ORDER_MARGIN
                        and rules.needs_threshold(margin, required)
                    ),
                    has_liquor_tax=any(item.result.liquor_tax for item in lines),
                    alerts=rules.order_alerts(pairs, self.policy),
                    line_margins={
                        item.id: rules.line_margin(item.line, item.result) for item in lines
                    },
                    line_alerts={
                        item.id: rules.line_alerts(item.line, item.result, self.policy)
                        for item in lines
                    },
                )
            )
        return orders

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
        operation_center: str | None = None,
        margin_threshold_points: Decimal | None = None,
        sort: str = "attention",
        page: int = 1,
        size: int = 50,
    ) -> OrderPage:
        """The orders of a file, each with its lines.

        Args:
            batch_id (UUID): The uploaded file.
            status (str | None): Only orders ready for release or pending.
            reason (str | None): Only orders with at least one line for this reason.
            q (str | None): Free text over document, client, supplier and SKU.
            operation_center (str | None): Only the orders of this operation center
                (C.O. factura, the order header's).
            margin_threshold_points (Decimal | None): Try another margin threshold, in points,
                without saving it: statuses, counts and margins come out as if the file had it.
                The saved threshold is used when omitted.
            sort (str): `attention` (default) puts the orders and lines with no support first,
                then by margin from lowest to highest; `file` keeps the file order.
            page (int): 1-based page.
            size (int): Page size, up to :data:`MAX_PAGE_SIZE`.

        Returns:
            OrderPage: The page, plus the order count per status before filtering by it.
        """

        self._bounds(page, size)
        if status is not None and status not in vocabulary.ORDER_STATUSES:
            raise InvalidInputError(
                f"status debe ser uno de: {', '.join(vocabulary.ORDER_STATUSES)}"
            )
        if reason is not None and reason not in vocabulary.REASONS:
            raise InvalidInputError(f"reason debe ser uno de: {', '.join(vocabulary.REASONS)}")
        if sort not in SORTS:
            raise InvalidInputError(f"sort debe ser uno de: {', '.join(SORTS)}")
        batch = await self.get_batch(batch_id)
        tried = self._threshold(margin_threshold_points)
        threshold = batch.margin_threshold if tried is None else tried

        orders = await self._orders_of(batch_id, threshold)
        # Whether the threshold releases anything is a fact of the file, not of the search.
        threshold_released = sum(1 for order in orders if order.released_by_threshold)
        # The centers of the whole file, so the filter always offers all of them.
        centers = Counter(center for order in orders if (center := order.first.operation_center))
        term = " ".join((q or "").lower().split())
        if term:
            orders = [order for order in orders if _matches(order, term)]
        center_filter = (operation_center or "").strip()
        if center_filter:
            orders = [order for order in orders if order.first.operation_center == center_filter]
        if reason:
            orders = [
                order
                for order in orders
                if any(item.result.reason == reason for item in order.lines)
            ]
        counts = {value: 0 for value in vocabulary.ORDER_STATUSES}
        for order in orders:
            counts[order.status] += 1
        if status:
            orders = [order for order in orders if order.status == status]
        if sort == "attention":
            orders = [_attention_lines(order) for order in sorted(orders, key=_order_key)]
        start = (page - 1) * size
        return OrderPage(
            items=orders[start : start + size],
            total=len(orders),
            page=page,
            size=size,
            counts=counts,
            threshold_released_count=threshold_released,
            operation_centers=tuple(sorted(centers.items())),
        )

    def rule_catalog(self) -> tuple[Section, ...]:
        """The rules the validator applies, with the values in force, for people to read."""

        return catalog(self.policy)

    @staticmethod
    def catalogs() -> dict[str, list[dict[str, str]]]:
        """Statuses and reasons with the labels the screens show."""

        return {
            "statuses": [
                {"value": value, "label": vocabulary.STATUS_LABELS[value]}
                for value in vocabulary.ORDER_STATUSES
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
            "alerts": [
                {"value": value, "label": vocabulary.ALERT_LABELS[value]}
                for value in vocabulary.ALERTS
            ],
        }

    @staticmethod
    def _bounds(page: int, size: int) -> None:
        if page < 1:
            raise InvalidInputError("La página debe ser mayor o igual a 1")
        if not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"El tamaño debe estar entre 1 y {MAX_PAGE_SIZE}")


__all__ = ["MAX_PAGE_SIZE", "SORTS", "OrderValidationService"]
