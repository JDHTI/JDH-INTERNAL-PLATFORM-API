"""PostgreSQL reads of the board of Listas principales: the latest update of every main list
read as one table, filtered, counted, sorted and paged in the database."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    and_,
    bindparam,
    case,
    delete,
    exists,
    func,
    insert,
    or_,
    select,
    true,
    update,
)

from app.infrastructure.database import Database
from app.modules.quotations.domain.board import (
    MAX_KEYS,
    NO_LIST_PRICE,
    TARGET_TOLERANCE,
    BoardFilter,
    BoardKey,
    BoardRow,
    WarehouseStock,
)
from app.modules.quotations.domain.entities import Quotation
from app.modules.quotations.domain.pricing import MAIN_LIST_WAREHOUSES
from app.modules.quotations.domain.vocabulary import LOCAL, TO_VALIDATE, UNMARKED, VALIDATED
from app.modules.quotations.infrastructure.models import (
    MainListStockModel,
    QuotationLineModel,
    QuotationModel,
)
from app.modules.quotations.infrastructure.repository import _line, _quotation

Q = QuotationModel
L = QuotationLineModel

_MARGIN = L.current_margin_with_support_pct
_TARGET = L.target_margin_pct
_JUDGED = and_(L.last_price.is_not(None), _MARGIN.is_not(None), _TARGET.is_not(None))
# What goes up and what down (BI, 2026-11-26): the highest cost (quotation, inventory or the
# pending increase, with the list's factor) at the minimum margin gives the floor price,
# ``ceil(cost / (1 − minimum margin))``. A current price under it goes up; otherwise down.
# Until then the trend compared the new price with the current one, and as almost no row has a
# new price, 26,484 of 26,518 rows were «estable».
_FLOOR = func.ceil(L.cost / (1 - L.min_margin_pct))
_FLOORED = and_(
    L.last_price.is_not(None),
    L.cost.is_not(None),
    L.min_margin_pct.is_not(None),
    L.min_margin_pct < 1,
)
_UP = and_(_FLOORED, L.last_price < _FLOOR)
_DOWN = and_(_FLOORED, L.last_price >= _FLOOR)
_CHIP_CONDITIONS = {
    "sin_marcar": and_(L.review_status == UNMARKED, L.load_id.is_(None)),
    "por_validar": and_(L.review_status == TO_VALIDATE, L.load_id.is_(None)),
    "validada": and_(L.review_status == VALIDATED, L.load_id.is_(None)),
    "cargada": L.load_id.is_not(None),
    # The same rule as «sube» (2026-11-26).
    "alza": _UP,
    # The three margin chips split the rows that can be judged: under the floor («alza»),
    # covering it up to half a point over the target («objetivo», under the target too) and
    # more than that («encima»).
    "objetivo": and_(_JUDGED, ~_UP, _MARGIN <= _TARGET + TARGET_TOLERANCE),
    "encima": and_(_JUDGED, ~_UP, _MARGIN > _TARGET + TARGET_TOLERANCE),
    "alerta": and_(
        L.is_active,
        or_(L.below_target, L.cost.is_(None), L.problems.contains(["precio_marcador"])),
    ),
}
# Without a current price a row is in none of them: it is «sin precio»; without a cost or a
# minimum margin it cannot be judged either.
_TREND_CONDITIONS = {"sube": _UP, "baja": _DOWN}
_NO_LIST_PRICE = L.last_price.is_(None)
_SORT_COLUMNS = {
    "sku": (L.sku,),
    "precio_actual": (L.last_price,),
    "margen_actual": (_MARGIN,),
    "precio_nuevo": (L.new_price,),
    "variacion": (L.variation_pct,),
    "existencia": (L.stock_on_hand,),
}


S = MainListStockModel
_CHUNK = 2000


class WarehouseStockRow(Protocol):
    """A SKU's stock in one warehouse (the quoter's ``StockRow``)."""

    sku: str
    warehouse: str
    warehouse_name: str | None
    on_hand: Decimal
    available: Decimal | None


def _in_warehouses(warehouses: Sequence[str]) -> ColumnElement[bool]:
    """The row has stock in one of the warehouses (as the sync read it)."""

    return exists().where(
        S.quotation_id == L.quotation_id,
        S.sku == L.sku,
        S.warehouse.in_(list(warehouses)),
        S.on_hand > 0,
    )


def _warehouse_stock(warehouses: Sequence[str]) -> Any:
    """The row's stock in the warehouses, for sorting."""

    return (
        select(func.sum(S.on_hand))
        .where(S.quotation_id == L.quotation_id, S.sku == L.sku, S.warehouse.in_(list(warehouses)))
        .correlate(L)
        .scalar_subquery()
    )


async def _breakdown(
    session: Any, keys: Sequence[tuple[UUID, str]]
) -> dict[tuple[UUID, str], tuple[WarehouseStock, ...]]:
    """The stock by warehouse of the page's rows, the largest first."""

    if not keys:
        return {}
    ids = sorted({quotation_id for quotation_id, _ in keys})
    skus = sorted({sku for _, sku in keys})
    found = (
        await session.execute(
            select(S)
            .where(S.quotation_id.in_(ids), S.sku.in_(skus))
            .order_by(S.quotation_id, S.sku, S.on_hand.desc(), S.warehouse)
        )
    ).scalars()
    wanted = set(keys)
    result: dict[tuple[UUID, str], list[WarehouseStock]] = {}
    for item in found:
        key = (item.quotation_id, item.sku)
        if key in wanted:
            result.setdefault(key, []).append(
                WarehouseStock(item.warehouse, item.warehouse_name, item.on_hand, item.available)
            )
    return {key: tuple(items) for key, items in result.items()}


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _facets(board: BoardFilter) -> tuple[ColumnElement[bool], ...]:
    """The chip, the trend and «sin precio» of the filter, each ``true`` when not set."""

    return (
        _CHIP_CONDITIONS[board.chip] if board.chip else true(),
        _TREND_CONDITIONS[board.trend] if board.trend else true(),
        _NO_LIST_PRICE if board.without_list_price else true(),
    )


def _zero_counts() -> dict[str, int]:
    return {
        **{chip: 0 for chip in _CHIP_CONDITIONS},
        **{trend: 0 for trend in _TREND_CONDITIONS},
        NO_LIST_PRICE: 0,
        "todos": 0,
    }


class SqlAlchemyBoardRepository:
    """The board's reads over ``quotations`` and ``quotation_lines``."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def current_updates(self, kind: str) -> list[Quotation]:
        """The latest update of every list of the kind, by list code."""

        async with self.database.session_factory() as session:
            rows = (
                await session.scalars(
                    select(Q)
                    .where(Q.kind == kind)
                    .distinct(Q.price_list_id)
                    .order_by(Q.price_list_id, Q.created_at.desc())
                )
            ).all()
        return [_quotation(row) for row in rows]

    def _base(self, quotation_ids: Sequence[UUID], board: BoardFilter) -> list:
        conditions = [L.quotation_id.in_(list(quotation_ids))]
        if board.warehouses:
            # Only the lists that have one of the warehouses (2026-11-26).
            lists = sorted(
                code
                for code, own in MAIN_LIST_WAREHOUSES.items()
                if set(own) & set(board.warehouses)
            )
            conditions.append(Q.price_list_id.in_(lists))
            if board.in_stock:
                # In stock in one of them, as the sync read it by warehouse.
                conditions.append(_in_warehouses(board.warehouses))
        elif board.in_stock:
            # Only what is in stock (2026-11-23; the default, a quick filter since 2026-11-25):
            # a SKU the sync read at 0 is left out; one whose stock was not read (``None``)
            # stays, so a DWH outage hides nothing.
            conditions.append(or_(L.stock_on_hand.is_(None), L.stock_on_hand > 0))
        if board.lists:
            conditions.append(Q.price_list_id.in_(list(board.lists)))
        if board.q:
            term = board.q.strip()
            pattern = f"%{_escape(term)}%"
            conditions.append(
                or_(
                    L.sku.startswith(term, autoescape=True),
                    L.description.ilike(pattern, escape="\\"),
                )
            )
        if board.suppliers:
            conditions.append(L.supplier_name.in_(list(board.suppliers)))
        if board.categories:
            conditions.append(L.category.in_(list(board.categories)))
        return conditions

    async def rows(
        self, quotation_ids: Sequence[UUID], board: BoardFilter, page: int, size: int
    ) -> tuple[list[BoardRow], int, dict[str, int]]:
        """One page of the board, its total and the rows of each chip, trend and
        «sin precio».

        Each one is counted under every filter but its own group, so each says how many rows
        it would show: the chips without the chip; the trends and «sin precio», one group of
        options, without the trend nor «sin precio». ``todos`` is every row under the
        filters but the chip.
        """

        if not quotation_ids:
            return [], 0, _zero_counts()
        base = self._base(quotation_ids, board)
        chip, trend, unpriced = _facets(board)
        filtered = [*base, chip, trend, unpriced]
        sort = _SORT_COLUMNS.get(board.sort)
        if board.sort == "existencia" and board.warehouses:
            # The stock of the warehouses chosen, not of the whole list.
            sort = (_warehouse_stock(board.warehouses),)
        if sort:
            order = [
                (column.desc() if board.descending else column.asc()).nulls_last()
                for column in sort
            ]
        else:
            order = []
        order += [Q.price_list_id, L.position]
        async with self.database.session_factory() as session:
            counts_row = (
                await session.execute(
                    select(
                        func.count().filter(and_(trend, unpriced)).label("todos"),
                        *(
                            func.count().filter(and_(condition, trend, unpriced)).label(name)
                            for name, condition in _CHIP_CONDITIONS.items()
                        ),
                        # The trends and «sin precio» are one group of options: counted
                        # under the chip and the rest, not under any of them.
                        *(
                            func.count().filter(and_(condition, chip)).label(name)
                            for name, condition in _TREND_CONDITIONS.items()
                        ),
                        func.count().filter(and_(_NO_LIST_PRICE, chip)).label(NO_LIST_PRICE),
                    )
                    .select_from(L)
                    .join(Q, Q.id == L.quotation_id)
                    .where(*base)
                )
            ).one()
            total = (
                await session.execute(
                    select(func.count())
                    .select_from(L)
                    .join(Q, Q.id == L.quotation_id)
                    .where(*filtered)
                )
            ).scalar_one()
            found = (
                await session.execute(
                    select(L, Q.id, Q.version, Q.price_list_id, Q.price_list_name)
                    .join(Q, Q.id == L.quotation_id)
                    .where(*filtered)
                    .order_by(*order)
                    .offset((page - 1) * size)
                    .limit(size)
                )
            ).all()
            breakdown = await _breakdown(
                session, [(quotation_id, line.sku) for line, quotation_id, *_ in found]
            )
        rows = [
            BoardRow(
                quotation_id=quotation_id,
                quotation_version=version,
                price_list_id=list_id or "",
                price_list_name=list_name,
                line=_line(line),
                stock_on_hand=line.stock_on_hand,
                stock_available=line.stock_available,
                stock_by_warehouse=breakdown.get((quotation_id, line.sku), ()),
            )
            for line, quotation_id, version, list_id, list_name in found
        ]
        counts = {name: int(getattr(counts_row, name) or 0) for name in _zero_counts()}
        return rows, int(total), counts

    async def keys(self, quotation_ids: Sequence[UUID], board: BoardFilter) -> list[BoardKey]:
        """Every row of the filter (at most :data:`MAX_KEYS`), for an action on all of them."""

        if not quotation_ids:
            return []
        conditions = [*self._base(quotation_ids, board), *_facets(board)]
        async with self.database.session_factory() as session:
            found = (
                await session.execute(
                    select(L.quotation_id, L.sku)
                    .join(Q, Q.id == L.quotation_id)
                    .where(*conditions)
                    .order_by(Q.price_list_id, L.position)
                    .limit(MAX_KEYS)
                )
            ).all()
        return [BoardKey(quotation_id, sku) for quotation_id, sku in found]

    async def list_counts(self, quotation_ids: Sequence[UUID]) -> dict[UUID, dict[str, int]]:
        """Rows of each review state, and below target, per update."""

        if not quotation_ids:
            return {}
        review = case(
            (L.load_id.is_not(None), "cargada"),
            else_=L.review_status,
        )
        async with self.database.session_factory() as session:
            found = (
                await session.execute(
                    select(L.quotation_id, review, func.count())
                    .where(L.quotation_id.in_(list(quotation_ids)))
                    .group_by(L.quotation_id, review)
                )
            ).all()
        result: dict[UUID, dict[str, int]] = {}
        for quotation_id, state, count in found:
            result.setdefault(quotation_id, {})[str(state)] = int(count)
        return result

    async def set_stock(
        self,
        quotation_id: UUID,
        stock: dict[str, tuple[Decimal, Decimal | None]],
        breakdown: Sequence[WarehouseStockRow] = (),
    ) -> None:
        """Store the stock the sync read for an update: ``(on hand, available)`` by SKU (its
        other SIESA items have none, 0) and, in the same transaction, the stock by warehouse
        in place of the previous one."""

        table = L.__table__
        async with self.database.session_factory() as session, session.begin():
            await session.execute(delete(S).where(S.quotation_id == quotation_id))
            rows = [
                {
                    "quotation_id": quotation_id,
                    "sku": item.sku,
                    "warehouse": item.warehouse,
                    "warehouse_name": item.warehouse_name,
                    "on_hand": item.on_hand,
                    "available": item.available,
                }
                for item in {(row.sku, row.warehouse): row for row in breakdown}.values()
                if item.on_hand > 0
            ]
            for start in range(0, len(rows), _CHUNK):
                await session.execute(insert(S), rows[start : start + _CHUNK])
            await session.execute(
                update(table)
                .where(table.c.quotation_id == quotation_id, table.c.origin != LOCAL)
                .values(stock_on_hand=0, stock_available=0)
            )
            if stock:
                await session.execute(
                    update(table).where(
                        table.c.quotation_id == bindparam("owner_id"),
                        table.c.sku == bindparam("item_sku"),
                    ),
                    [
                        {
                            "owner_id": quotation_id,
                            "item_sku": sku,
                            "stock_on_hand": on_hand,
                            "stock_available": available,
                        }
                        for sku, (on_hand, available) in stock.items()
                    ],
                )

    async def options(self, quotation_ids: Sequence[UUID]) -> tuple[list[str], list[str]]:
        """The suppliers and categories of the board's rows, sorted."""

        if not quotation_ids:
            return [], []
        async with self.database.session_factory() as session:
            suppliers = (
                await session.scalars(
                    select(L.supplier_name)
                    .where(L.quotation_id.in_(list(quotation_ids)), L.supplier_name.is_not(None))
                    .distinct()
                )
            ).all()
            categories = (
                await session.scalars(
                    select(L.category)
                    .where(
                        L.quotation_id.in_(list(quotation_ids)),
                        L.category.is_not(None),
                        L.category != "",
                    )
                    .distinct()
                )
            ).all()
        return sorted(str(item) for item in suppliers), sorted(str(item) for item in categories)


__all__ = ["SqlAlchemyBoardRepository"]
