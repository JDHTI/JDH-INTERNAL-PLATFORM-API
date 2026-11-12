"""Item review, staging and loads of the list updates, and each item's detail.

A list update (``lista`` or ``principal``) is never confirmed as a whole. Each item goes:

- ``sin_marcar`` → ``por_validar``: whoever edits it says its price is ready;
- → ``validada``: whoever validates the kind (BI) accepts it; it waits in its list's
  **staging** until it is loaded in SIESA;
- loaded: the validator downloads the staging's files (SIESA's two pages, prices and value
  taxes), loads them and marks them loaded; the items keep ``validada`` and point to the load.

A new price sends the item back to ``sin_marcar`` and out of any load: a new cycle. Two updates
of the same list cannot both stage a SKU: the second validation is refused.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from app.core.errors import (
    DependencyUnavailableError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
)
from app.modules.identity.domain.principal import Principal
from app.modules.quotations.application.service import ExportFile, QuotationService, Skipped
from app.modules.quotations.domain import pricing, vocabulary
from app.modules.quotations.domain.entities import (
    LineInsight,
    PriceListLoad,
    QuotationDetail,
    StagedItem,
)
from app.modules.quotations.domain.export import ExportRow, ExportTaxRow, PriceListExport
from app.modules.quotations.domain.ports import (
    Calendar,
    ExportWriter,
    InsightSource,
    StagingRepository,
)

logger = logging.getLogger(__name__)
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# Months of sales the item's chart shows.
SALES_MONTHS = vocabulary.LIST_UPDATE_WINDOW
MAX_REVIEW_SKUS = 10_000


@dataclass(frozen=True)
class StagedList:
    """The staging of one list: its validated items not loaded yet."""

    price_list_id: str
    price_list_name: str | None
    items: tuple[StagedItem, ...]


@dataclass(frozen=True)
class StockTotal:
    on_hand: Decimal
    available: Decimal | None
    warehouses: int


class ListReviewService:
    """Review of the items, the staging by list and its loads, and the items' detail."""

    def __init__(
        self,
        quotations: QuotationService,
        repository: StagingRepository,
        insights: InsightSource,
        calendar: Calendar,
        workbook: ExportWriter,
        flat_file: ExportWriter,
    ) -> None:
        """Create the service.

        Args:
            quotations (QuotationService): Access rules and the updates themselves.
            repository (StagingRepository): Quotations, staging and loads.
            insights (InsightSource): Stock, list price history and monthly sales.
            calendar (Calendar): Bogotá calendar.
            workbook (ExportWriter): SIESA workbook (sheets ``Precios`` and ``Impuestos en
                valor``).
            flat_file (ExportWriter): SIESA import file (records ``0126`` and ``0127``).
        """

        self.quotations = quotations
        self.repository = repository
        self.insights = insights
        self.calendar = calendar
        self.workbook = workbook
        self.flat_file = flat_file

    # ------------------------------------------------------------ review

    async def mark(
        self,
        principal: Principal,
        quotation_id: UUID,
        version: int,
        skus: Sequence[str],
        status: str,
    ) -> tuple[QuotationDetail, tuple[Skipped, ...]]:
        """Set the review of some items.

        - ``por_validar`` and ``sin_marcar``: whoever edits the update.
        - ``validada``, or taking an item out of ``validada``: only who validates the kind.
        - An item needs an active SKU, a new price and a unit to be marked; a loaded item
          keeps its state until its price changes; a SKU staged by another update of the
          list is not validated again. Those are skipped and reported; the rest is saved.

        Raises:
            ForbiddenError: Validating, or unvalidating, without validating the kind.
            InvalidInputError: Not a list update, an unknown state or SKU, or nothing could
                be marked.
        """

        if status not in vocabulary.REVIEW_STATUSES:
            raise InvalidInputError("El estado es sin_marcar, por_validar o validada")
        wanted = list(dict.fromkeys(sku.strip() for sku in skus if sku and sku.strip()))
        if not wanted:
            raise InvalidInputError("Elige las referencias a marcar")
        if len(wanted) > MAX_REVIEW_SKUS:
            raise InvalidInputError(f"Máximo {MAX_REVIEW_SKUS} referencias por vez")
        detail = await self.quotations.editable(principal, quotation_id, any_status=True)
        quotation = detail.quotation
        _list_only(quotation.is_list_update)
        self.quotations.check_version(quotation, version)
        validator = await self.quotations.can_validate(principal, quotation.kind)
        by_sku = {line.sku: line for line in detail.lines}
        unknown = [sku for sku in wanted if sku not in by_sku]
        if unknown:
            raise InvalidInputError(f"La referencia {unknown[0]} no está en la actualización")
        lines = [by_sku[sku] for sku in wanted]
        if not validator and (
            status == vocabulary.VALIDATED
            or any(line.review_status == vocabulary.VALIDATED for line in lines)
        ):
            raise ForbiddenError("Solo quien valida las listas valida o desvalida un ítem")
        elsewhere: dict[str, UUID] = {}
        if status == vocabulary.VALIDATED:
            elsewhere = await self.repository.staged_elsewhere(
                quotation.price_list_id or "", wanted, quotation.id
            )
        now = datetime.now(UTC)
        updated = []
        skipped: list[Skipped] = []
        for line in lines:
            reason = None
            if line.load_id is not None:
                reason = "Ya se cargó en SIESA: cambia su precio para empezar otra revisión"
            elif status != vocabulary.UNMARKED and not line.is_active:
                reason = "Referencia inactiva en SIESA"
            elif status != vocabulary.UNMARKED and line.new_price is None:
                reason = "Sin precio nuevo"
            elif status != vocabulary.UNMARKED and not line.unit:
                reason = "Sin unidad de medida"
            elif line.sku in elsewhere:
                reason = "Ya está validada y sin cargar en otra actualización de esta lista"
            if reason is not None:
                skipped.append(Skipped(line.sku, reason))
                continue
            if line.review_status == status:
                continue
            unmarked = status == vocabulary.UNMARKED
            updated.append(
                replace(
                    line,
                    review_status=status,
                    reviewed_by=None if unmarked else principal.email,
                    reviewed_at=None if unmarked else now,
                )
            )
        if not updated:
            if skipped:
                raise InvalidInputError(
                    "Ninguna referencia se pudo marcar: "
                    + "; ".join(f"{item.sku} ({item.reason})" for item in skipped[:10])
                )
            return detail, ()
        saved = await self.repository.save(
            self.quotations.bumped(quotation, principal.email), updated, version
        )
        return saved, tuple(skipped)

    async def set_note(
        self, principal: Principal, quotation_id: UUID, version: int, sku: str, note: str | None
    ) -> QuotationDetail:
        """Write or clear the observation of an item (whoever edits the update, any state).

        Raises:
            InvalidInputError: Not a list update, an unknown SKU or a note too long.
        """

        detail = await self.quotations.editable(principal, quotation_id, any_status=True)
        _list_only(detail.quotation.is_list_update)
        self.quotations.check_version(detail.quotation, version)
        line = next((item for item in detail.lines if item.sku == sku), None)
        if line is None:
            raise InvalidInputError(f"La referencia {sku} no está en la actualización")
        text = " ".join((note or "").split()) or None
        if text is not None and len(text) > vocabulary.NOTE_LENGTH:
            raise InvalidInputError(
                f"La observación admite máximo {vocabulary.NOTE_LENGTH} caracteres"
            )
        if text == line.note:
            return detail
        return await self.repository.save(
            self.quotations.bumped(detail.quotation, principal.email),
            [replace(line, note=text)],
            version,
        )

    # ------------------------------------------------------------ staging

    async def _require_kind(self, principal: Principal, kind: str, validate: bool) -> None:
        if kind not in vocabulary.LIST_KINDS:
            raise InvalidInputError("Tipo de actualización desconocido: lista o principal")
        if validate:
            if not await self.quotations.can_validate(principal, kind):
                raise ForbiddenError("Solo quien valida las listas descarga y carga el staging")
        elif not await self.quotations.can_read(principal, kind):
            raise ForbiddenError("No tienes permiso para consultar estas listas")

    async def staging(self, principal: Principal, kind: str) -> list[StagedList]:
        """Every list with validated items not loaded yet, for whoever reads the kind."""

        await self._require_kind(principal, kind, validate=False)
        groups: dict[str, list[StagedItem]] = {}
        names: dict[str, str | None] = {}
        for item in await self.repository.staged(kind):
            groups.setdefault(item.price_list_id, []).append(item)
            names.setdefault(item.price_list_id, item.price_list_name)
        return [
            StagedList(code, names[code], tuple(items)) for code, items in sorted(groups.items())
        ]

    def _activation(self, value: date | None) -> date:
        today = self.calendar.today()
        activation = value or today + timedelta(days=1)
        if activation < today:
            raise InvalidInputError("La fecha de activación no puede ser anterior a hoy")
        return activation

    async def _staged_list(self, kind: str, price_list_id: str) -> list[StagedItem]:
        items = await self.repository.staged(kind, price_list_id.strip())
        if not items:
            raise NotFoundError(f"La lista {price_list_id} no tiene ítems por cargar")
        return items

    @staticmethod
    def _export(items: Sequence[StagedItem], activation: date) -> PriceListExport:
        """The staged items as SIESA's two pages; a SKU staged twice takes its latest."""

        latest: dict[str, StagedItem] = {}
        for item in items:
            current = latest.get(item.line.sku)
            stamp = item.line.reviewed_at or datetime.min.replace(tzinfo=UTC)
            if current is None or stamp >= (
                current.line.reviewed_at or datetime.min.replace(tzinfo=UTC)
            ):
                latest[item.line.sku] = item
        rows: list[ExportRow] = []
        taxes: list[ExportTaxRow] = []
        for item in sorted(latest.values(), key=lambda value: value.line.sku):
            line = item.line
            unit = line.unit or ""
            rows.append(
                ExportRow(item.price_list_id, line.sku, activation, unit, line.new_price)  # type: ignore[arg-type]
            )
            if line.value_tax_key and line.value_tax_amount:
                taxes.append(
                    ExportTaxRow(
                        item.price_list_id,
                        line.sku,
                        activation,
                        unit,
                        line.value_tax_key,
                        line.value_tax_amount,
                    )
                )
        return PriceListExport(tuple(rows), tuple(taxes))

    async def staging_file(
        self,
        principal: Principal,
        kind: str,
        price_list_id: str,
        fmt: str,
        activation_date: date | None,
    ) -> ExportFile:
        """The staging of one list as the SIESA workbook (``xlsx``) or import file (``txt``).

        Raises:
            ForbiddenError: The caller does not validate the kind.
            NotFoundError: The list has nothing to load.
        """

        await self._require_kind(principal, kind, validate=True)
        activation = self._activation(activation_date)
        items = await self._staged_list(kind, price_list_id)
        export = self._export(items, activation)
        code = items[0].price_list_id
        stamp = activation.strftime("%Y%m%d")
        if fmt == "xlsx":
            return ExportFile(
                await asyncio.to_thread(self.workbook.write, export),
                f"ACTUALIZACION LISTA {code}_{stamp}.xlsx",
                _XLSX,
            )
        if fmt != "txt":
            raise InvalidInputError("Formato desconocido: xlsx o txt")
        return ExportFile(
            await asyncio.to_thread(self.flat_file.write, export),
            f"precios-lista{code}-staging-{stamp}.txt",
            "text/plain; charset=us-ascii",
        )

    async def load(
        self,
        principal: Principal,
        kind: str,
        price_list_id: str,
        line_ids: Sequence[UUID],
        activation_date: date | None,
    ) -> PriceListLoad:
        """Mark the given staged items of a list as loaded in SIESA.

        ``line_ids`` are the items of the files the validator downloaded; one that left the
        staging meanwhile (its price changed, or it was loaded) refuses the whole load.

        Raises:
            ForbiddenError: The caller does not validate the kind.
            NotFoundError: The list has nothing to load.
            ConflictError: An item is not in the staging any more.
        """

        await self._require_kind(principal, kind, validate=True)
        activation = self._activation(activation_date)
        wanted = list(dict.fromkeys(line_ids))
        if not wanted:
            raise InvalidInputError("Elige los ítems que se cargaron")
        items = await self._staged_list(kind, price_list_id)
        by_id = {item.line.id: item for item in items}
        missing = [line_id for line_id in wanted if line_id not in by_id]
        if missing:
            raise InvalidInputError(
                "Hay ítems que ya no están por cargar: recarga la lista y descarga los archivos "
                "de nuevo"
            )
        chosen = [by_id[line_id] for line_id in wanted]
        rows = tuple(
            {
                "quotation_id": str(item.quotation_id),
                "line_id": str(item.line.id),
                "sku": item.line.sku,
                "description": item.line.description,
                "unit": item.line.unit,
                "previous_price": _text(item.line.last_price),
                "price": _text(item.line.new_price),
                "tax_key": item.line.value_tax_key,
                "tax_amount": _text(item.line.value_tax_amount),
                "validated_by": item.line.reviewed_by,
            }
            for item in chosen
        )
        load = PriceListLoad(
            id=uuid4(),
            kind=kind,
            price_list_id=chosen[0].price_list_id,
            price_list_name=chosen[0].price_list_name,
            activation_date=activation,
            line_count=len(chosen),
            rows=rows,
            created_by=principal.email,
            created_at=datetime.now(UTC),
        )
        return await self.repository.create_load(load, wanted)

    async def loads(self, principal: Principal, kind: str, limit: int = 30) -> list[PriceListLoad]:
        """The latest loads of the kind, newest first."""

        await self._require_kind(principal, kind, validate=False)
        return await self.repository.loads(kind, max(1, min(limit, 100)))

    # ------------------------------------------------------------ detail

    async def insight(self, principal: Principal, quotation_id: UUID, sku: str) -> LineInsight:
        """An item's stock by warehouse, its list price history and the monthly sale price
        to the list's customers (to the customer, for an update started from one).

        The sales are a convenience: when the warehouse does not answer them in time the
        rest comes with ``sales_read`` false.
        """

        detail = await self.quotations.get(principal, quotation_id)
        quotation = detail.quotation
        _list_only(quotation.is_list_update)
        line = next((item for item in detail.lines if item.sku == sku), None)
        if line is None:
            raise NotFoundError(f"La referencia {sku} no está en la actualización")
        warehouses = pricing.stock_warehouses(quotation.price_list_id, quotation.cost_centers)
        stock, history = await asyncio.gather(
            self.insights.stock([sku], warehouses),
            self.insights.price_history(
                quotation.price_list_id or "", sku, vocabulary.PRICE_HISTORY_MONTHS
            ),
        )
        audience = await self.quotations.audience_of(quotation)
        keys = audience.sale_keys if audience is not None else None
        scope = "cliente" if quotation.customer_nit else ("compania" if keys is None else "lista")
        try:
            sales = await self.insights.monthly_sales(sku, SALES_MONTHS, keys)
            read = True
        except DependencyUnavailableError as exc:
            logger.warning("quotations: monthly sales of %s not read: %s", sku, exc)
            sales, read = [], False
        return LineInsight(
            sku=sku,
            warehouses=warehouses,
            stock=tuple(stock),
            history=tuple(history),
            sales=tuple(sales),
            sales_scope=scope,
            sales_read=read,
        )

    async def stock_totals(
        self, principal: Principal, quotation_id: UUID
    ) -> tuple[tuple[str, ...], dict[str, StockTotal]]:
        """Stock of every item in the update's warehouses, summed per SKU."""

        detail = await self.quotations.get(principal, quotation_id)
        quotation = detail.quotation
        _list_only(quotation.is_list_update)
        warehouses = pricing.stock_warehouses(quotation.price_list_id, quotation.cost_centers)
        skus = [line.sku for line in detail.lines if not line.is_local]
        totals: dict[str, StockTotal] = {}
        for row in await self.insights.stock(skus, warehouses):
            current = totals.get(row.sku)
            available = row.available
            if current is None:
                totals[row.sku] = StockTotal(row.on_hand, available, 1)
                continue
            totals[row.sku] = StockTotal(
                current.on_hand + row.on_hand,
                None
                if current.available is None or available is None
                else current.available + available,
                current.warehouses + 1,
            )
        return warehouses, totals


def _list_only(is_list_update: bool) -> None:
    if not is_list_update:
        raise InvalidInputError("Solo las actualizaciones de lista se revisan por ítem")


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


__all__ = ["ListReviewService", "StagedList", "StockTotal"]
