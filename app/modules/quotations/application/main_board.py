"""The board of Listas principales: every main list in one table.

Underneath, each main list keeps one current update (its latest): the quoter's engine, the
review of its items, its staging and files. The board builds or refreshes those updates list
by list and reads their lines together, filtered and paged in the database; every edit, mark
or note is sent to the update the row belongs to, at its current version.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.core.errors import (
    ConflictError,
    DependencyUnavailableError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
)
from app.modules.identity.domain.principal import Principal
from app.modules.quotations.application.list_review import ListReviewService, StockTotal
from app.modules.quotations.application.service import LineChange, QuotationService, Skipped
from app.modules.quotations.domain import board, pricing, vocabulary
from app.modules.quotations.domain.board import (
    BoardFilter,
    BoardKey,
    BoardList,
    BoardPage,
    BoardRow,
)
from app.modules.quotations.domain.entities import Quotation
from app.modules.quotations.domain.ports import InsightSource

logger = logging.getLogger(__name__)
KIND = vocabulary.MAIN_LIST
APPLY_MODES = ("optimo", "minimo", "limpiar")


class BoardRepository(Protocol):
    async def current_updates(self, kind: str) -> list[Quotation]: ...

    async def rows(
        self, quotation_ids: Sequence[UUID], board: BoardFilter, page: int, size: int
    ) -> tuple[list[BoardRow], int, dict[str, int]]: ...

    async def keys(self, quotation_ids: Sequence[UUID], board: BoardFilter) -> list[BoardKey]: ...

    async def list_counts(self, quotation_ids: Sequence[UUID]) -> dict[UUID, dict[str, int]]: ...

    async def options(self, quotation_ids: Sequence[UUID]) -> tuple[list[str], list[str]]: ...


@dataclass(frozen=True)
class BoardOverview:
    lists: tuple[BoardList, ...]
    suppliers: tuple[str, ...]
    categories: tuple[str, ...]


@dataclass(frozen=True)
class BoardResult:
    page: BoardPage
    # Stock of the page's rows in their list's warehouses; ``None`` when it was not read.
    stock: dict[tuple[UUID, str], StockTotal] | None


@dataclass(frozen=True)
class PriceInput:
    quotation_id: UUID
    sku: str
    new_price: Decimal | None


class MainBoardService:
    """Build, read and edit the main lists as one board."""

    def __init__(
        self,
        quotations: QuotationService,
        review: ListReviewService,
        repository: BoardRepository,
        insights: InsightSource,
    ) -> None:
        """Create the service.

        Args:
            quotations (QuotationService): The updates and their access rules.
            review (ListReviewService): Review and notes of the items.
            repository (BoardRepository): The board's reads.
            insights (InsightSource): Stock of the page's rows.
        """

        self.quotations = quotations
        self.review = review
        self.repository = repository
        self.insights = insights

    @property
    def codes(self) -> tuple[str, ...]:
        """The main lists in the configured order."""

        return tuple(self.quotations.main_list_order)

    async def _require(self, principal: Principal, manage: bool = False) -> None:
        allowed = (
            await self.quotations.can_manage(principal, KIND)
            if manage
            else await self.quotations.can_read(principal, KIND)
        )
        if not allowed:
            raise ForbiddenError(
                "No tienes permiso para actualizar las listas principales"
                if manage
                else "No tienes permiso para consultar las listas principales"
            )

    async def _current(self) -> dict[str, Quotation]:
        """The current update of each main list, by code."""

        codes = set(self.codes)
        return {
            item.price_list_id or "": item
            for item in await self.repository.current_updates(KIND)
            if (item.price_list_id or "") in codes
        }

    async def overview(self, principal: Principal) -> BoardOverview:
        """Every main list, with its update's counts when it was built, and the suppliers and
        categories the board can be filtered by."""

        await self._require(principal)
        current = await self._current()
        ids = [item.id for item in current.values()]
        counts = await self.repository.list_counts(ids)
        lists = []
        for code in self.codes:
            update = current.get(code)
            if update is None:
                name = None
                try:
                    info = await self.quotations.warehouse.price_list(code)
                    name = info.name if info else None
                except DependencyUnavailableError:
                    logger.warning("quotations: name of list %s not read", code)
                lists.append(BoardList(code=code, name=name))
                continue
            lists.append(
                BoardList(
                    code=code,
                    name=update.price_list_name,
                    quotation_id=update.id,
                    built_at=update.built_at,
                    line_count=update.line_count,
                    counts=counts.get(update.id, {}),
                    warnings=update.warnings,
                )
            )
        suppliers, categories = await self.repository.options(ids)
        return BoardOverview(tuple(lists), tuple(suppliers), tuple(categories))

    async def build(self, principal: Principal, code: str) -> BoardList:
        """Build a main list's update, or refresh the one it has (new prices kept by SKU).

        Raises:
            InvalidInputError: Not a main list.
        """

        await self._require(principal, manage=True)
        if code not in self.codes:
            raise InvalidInputError(f"La lista {code} no es una lista principal")
        update = (await self._current()).get(code)
        if update is None:
            detail = await self.quotations.create_list_update(
                principal, price_list_id=code, kind=KIND
            )
        else:
            detail = await self.quotations.refresh(principal, update.id, update.version)
        quotation = detail.quotation
        counts = await self.repository.list_counts([quotation.id])
        return BoardList(
            code=code,
            name=quotation.price_list_name,
            quotation_id=quotation.id,
            built_at=quotation.built_at,
            line_count=quotation.line_count,
            counts=counts.get(quotation.id, {}),
            warnings=quotation.warnings,
        )

    def _checked(self, filters: BoardFilter) -> BoardFilter:
        if filters.chip is not None and filters.chip not in board.CHIPS:
            raise InvalidInputError("Filtro desconocido")
        if filters.sort not in board.SORTS:
            raise InvalidInputError("Orden desconocido")
        unknown = [code for code in filters.lists if code not in self.codes]
        if unknown:
            raise InvalidInputError(f"La lista {unknown[0]} no es una lista principal")
        return filters

    async def rows(
        self, principal: Principal, filters: BoardFilter, page: int, size: int
    ) -> BoardResult:
        """One page of the board and the stock of its rows.

        The stock is a convenience: when the warehouse does not answer, the page comes
        without it.
        """

        await self._require(principal)
        if page < 1 or not 1 <= size <= board.MAX_PAGE_SIZE:
            raise InvalidInputError(f"Página inválida; el tamaño máximo es {board.MAX_PAGE_SIZE}")
        filters = self._checked(filters)
        current = await self._current()
        by_id = {item.id: item for item in current.values()}
        rows, total, counts = await self.repository.rows(list(by_id), filters, page, size)
        result = BoardPage(tuple(rows), total, page, size, counts)
        try:
            stock = await self._stock(rows, by_id)
        except DependencyUnavailableError as exc:
            logger.warning("quotations: stock of the board not read: %s", exc)
            stock = None
        return BoardResult(result, stock)

    async def _stock(
        self, rows: Sequence[BoardRow], updates: dict[UUID, Quotation]
    ) -> dict[tuple[UUID, str], StockTotal]:
        """Stock of each row in its list's sellable warehouses, one read per list."""

        skus: dict[UUID, list[str]] = defaultdict(list)
        for row in rows:
            if not row.line.is_local:
                skus[row.quotation_id].append(row.line.sku)
        totals: dict[tuple[UUID, str], StockTotal] = {}
        for quotation_id, items in skus.items():
            update = updates[quotation_id]
            warehouses = pricing.stock_warehouses(update.price_list_id, update.cost_centers)
            for stock_row in await self.insights.stock(items, warehouses):
                key = (quotation_id, stock_row.sku)
                current = totals.get(key)
                if current is None:
                    totals[key] = StockTotal(stock_row.on_hand, stock_row.available, 1)
                    continue
                totals[key] = StockTotal(
                    current.on_hand + stock_row.on_hand,
                    None
                    if current.available is None or stock_row.available is None
                    else current.available + stock_row.available,
                    current.warehouses + 1,
                )
        return totals

    async def keys(self, principal: Principal, filters: BoardFilter) -> list[BoardKey]:
        """Every row of the filter (at most 10,000), for an action on all of them."""

        await self._require(principal)
        current = await self._current()
        return await self.repository.keys(
            [item.id for item in current.values()], self._checked(filters)
        )

    async def _groups(self, keys: Sequence[BoardKey]) -> dict[UUID, list[str]]:
        """The SKUs of each update, checked to be on the board."""

        if not keys:
            raise InvalidInputError("Elige las referencias")
        if len(keys) > board.MAX_KEYS:
            raise InvalidInputError(f"Máximo {board.MAX_KEYS} referencias por vez")
        allowed = {item.id for item in (await self._current()).values()}
        groups: dict[UUID, list[str]] = defaultdict(list)
        for key in keys:
            if key.quotation_id not in allowed:
                raise NotFoundError("Una referencia ya no está en el tablero: recárgalo")
            groups[key.quotation_id].append(key.sku)
        return groups

    async def _version(self, quotation_id: UUID) -> int:
        detail = await self.quotations.repository.get(quotation_id)
        if detail is None:
            raise NotFoundError("Una lista del tablero ya no existe: recárgalo")
        return detail.quotation.version

    async def _each(self, quotation_id: UUID, action) -> tuple[Skipped, ...]:
        """Run an edit at the update's current version; once more if it moved meanwhile."""

        for attempt in range(2):
            try:
                return await action(await self._version(quotation_id))
            except ConflictError:
                if attempt == 1:
                    raise
        return ()  # pragma: no cover - the loop returns or raises

    async def set_prices(
        self, principal: Principal, changes: Sequence[PriceInput]
    ) -> tuple[int, tuple[Skipped, ...]]:
        """Write or clear new prices on rows of any list.

        Returns:
            tuple[int, tuple[Skipped, ...]]: Rows written and the ones that could not be.
        """

        await self._require(principal, manage=True)
        groups = await self._groups([BoardKey(item.quotation_id, item.sku) for item in changes])
        prices = {(item.quotation_id, item.sku): item.new_price for item in changes}
        skipped: list[Skipped] = []
        written = 0
        for quotation_id, skus in groups.items():
            lines = [LineChange(sku, prices[(quotation_id, sku)]) for sku in skus]

            async def run(version: int, lines=lines, quotation_id=quotation_id):
                _, missed = await self.quotations.update_lines(
                    principal, quotation_id, version, lines
                )
                return missed

            try:
                missed = await self._each(quotation_id, run)
            except InvalidInputError as exc:
                skipped += [Skipped(sku, str(exc)) for sku in skus]
                continue
            skipped += missed
            written += len(skus) - len(missed)
        return written, tuple(skipped)

    async def apply(
        self, principal: Principal, keys: Sequence[BoardKey], mode: str
    ) -> tuple[int, tuple[Skipped, ...]]:
        """Price rows at their optimal or minimum price, or clear their new price."""

        if mode not in APPLY_MODES:
            raise InvalidInputError("El modo es optimo, minimo o limpiar")
        await self._require(principal, manage=True)
        groups = await self._groups(keys)
        changes: list[PriceInput] = []
        skipped: list[Skipped] = []
        for quotation_id, skus in groups.items():
            detail = await self.quotations.repository.get(quotation_id)
            lines = {line.sku: line for line in detail.lines} if detail else {}
            for sku in skus:
                line = lines.get(sku)
                if line is None:
                    skipped.append(Skipped(sku, "Ya no está en la lista"))
                    continue
                if mode == "limpiar":
                    changes.append(PriceInput(quotation_id, sku, None))
                    continue
                price = line.optimal_price if mode == "optimo" else line.min_price
                if price is None:
                    skipped.append(Skipped(sku, "Sin costo: no hay precio sugerido"))
                    continue
                changes.append(PriceInput(quotation_id, sku, price.to_integral_value()))
        if not changes:
            return 0, tuple(skipped)
        written, missed = await self.set_prices(principal, changes)
        return written, (*skipped, *missed)

    async def mark(
        self, principal: Principal, keys: Sequence[BoardKey], status: str
    ) -> tuple[int, tuple[Skipped, ...]]:
        """Set the review of rows of any list (see :meth:`ListReviewService.mark`)."""

        await self._require(principal, manage=True)
        groups = await self._groups(keys)
        skipped: list[Skipped] = []
        marked = 0
        for quotation_id, skus in groups.items():

            async def run(version: int, skus=skus, quotation_id=quotation_id):
                _, missed = await self.review.mark(principal, quotation_id, version, skus, status)
                return missed

            try:
                missed = await self._each(quotation_id, run)
            except InvalidInputError as exc:
                skipped += [Skipped(sku, str(exc).split(": ", 1)[-1]) for sku in skus]
                continue
            skipped += missed
            marked += len(skus) - len(missed)
        return marked, tuple(skipped)

    async def note(
        self, principal: Principal, quotation_id: UUID, sku: str, text: str | None
    ) -> BoardRow:
        """Write or clear a row's observation; the row as it is now."""

        await self._require(principal, manage=True)
        await self._groups([BoardKey(quotation_id, sku)])
        saved = None

        async def run(version: int):
            nonlocal saved
            saved = await self.review.set_note(principal, quotation_id, version, sku, text)
            return ()

        await self._each(quotation_id, run)
        quotation = saved.quotation  # type: ignore[union-attr]
        line = next(item for item in saved.lines if item.sku == sku)  # type: ignore[union-attr]
        return BoardRow(
            quotation.id,
            quotation.version,
            quotation.price_list_id or "",
            quotation.price_list_name,
            replace(line),
        )


__all__ = ["APPLY_MODES", "BoardOverview", "BoardResult", "MainBoardService", "PriceInput"]
