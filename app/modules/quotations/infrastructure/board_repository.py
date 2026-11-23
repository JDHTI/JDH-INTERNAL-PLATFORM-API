"""PostgreSQL reads of the board of Listas principales: the latest update of every main list
read as one table, filtered, counted, sorted and paged in the database."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, bindparam, case, func, or_, select, update

from app.infrastructure.database import Database
from app.modules.quotations.domain.board import (
    MAX_KEYS,
    MIN_INCREASE,
    TARGET_TOLERANCE,
    BoardFilter,
    BoardKey,
    BoardRow,
)
from app.modules.quotations.domain.entities import Quotation
from app.modules.quotations.domain.vocabulary import LOCAL, TO_VALIDATE, UNMARKED, VALIDATED
from app.modules.quotations.infrastructure.models import QuotationLineModel, QuotationModel
from app.modules.quotations.infrastructure.repository import _line, _quotation

Q = QuotationModel
L = QuotationLineModel

_MARGIN = L.current_margin_with_support_pct
_TARGET = L.target_margin_pct
_JUDGED = and_(L.last_price.is_not(None), _MARGIN.is_not(None), _TARGET.is_not(None))
_BELOW = _MARGIN < _TARGET - TARGET_TOLERANCE
# The increase the price needs (2026-11-23): the new price chosen, else the optimal one,
# against the current price. Under :data:`MIN_INCREASE` it is not an increase.
_RISE = and_(
    L.last_price > 0,
    func.coalesce(L.new_price, L.optimal_price) >= L.last_price * (1 + MIN_INCREASE),
)
_CHIP_CONDITIONS = {
    "sin_marcar": and_(L.review_status == UNMARKED, L.load_id.is_(None)),
    "por_validar": and_(L.review_status == TO_VALIDATE, L.load_id.is_(None)),
    "validada": and_(L.review_status == VALIDATED, L.load_id.is_(None)),
    "cargada": L.load_id.is_not(None),
    "alza": and_(_JUDGED, _BELOW, _RISE),
    # Under the target but needing less than 1 % more is on target (2026-11-23).
    "objetivo": and_(
        _JUDGED,
        or_(
            _MARGIN.between(_TARGET - TARGET_TOLERANCE, _TARGET + TARGET_TOLERANCE),
            and_(_BELOW, ~_RISE),
        ),
    ),
    "encima": and_(_JUDGED, _MARGIN > _TARGET + TARGET_TOLERANCE),
    "alerta": and_(
        L.is_active,
        or_(L.below_target, L.cost.is_(None), L.problems.contains(["precio_marcador"])),
    ),
}
_SORT_COLUMNS = {
    "sku": (L.sku,),
    "precio_actual": (L.last_price,),
    "margen_actual": (_MARGIN,),
    "precio_nuevo": (L.new_price,),
    "variacion": (L.variation_pct,),
    "existencia": (L.stock_on_hand,),
}


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
        conditions = [
            L.quotation_id.in_(list(quotation_ids)),
            # Only what is in stock (2026-11-23): a SKU the sync read at 0 is left out; one
            # whose stock was not read (``None``) stays, so a DWH outage hides nothing.
            or_(L.stock_on_hand.is_(None), L.stock_on_hand > 0),
        ]
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
        """One page of the board, its total and the rows of each chip.

        The chips are counted under every filter but the chip itself, so each one says how
        many rows it would show.
        """

        if not quotation_ids:
            return [], 0, {**{chip: 0 for chip in _CHIP_CONDITIONS}, "todos": 0}
        base = self._base(quotation_ids, board)
        filtered = list(base)
        if board.chip:
            filtered.append(_CHIP_CONDITIONS[board.chip])
        sort = _SORT_COLUMNS.get(board.sort)
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
                        func.count().label("todos"),
                        *(
                            func.count().filter(condition).label(chip)
                            for chip, condition in _CHIP_CONDITIONS.items()
                        ),
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
        rows = [
            BoardRow(
                quotation_id=quotation_id,
                quotation_version=version,
                price_list_id=list_id or "",
                price_list_name=list_name,
                line=_line(line),
                stock_on_hand=line.stock_on_hand,
                stock_available=line.stock_available,
            )
            for line, quotation_id, version, list_id, list_name in found
        ]
        counts = {chip: int(getattr(counts_row, chip) or 0) for chip in _CHIP_CONDITIONS}
        counts["todos"] = int(counts_row.todos or 0)
        return rows, int(total), counts

    async def keys(self, quotation_ids: Sequence[UUID], board: BoardFilter) -> list[BoardKey]:
        """Every row of the filter (at most :data:`MAX_KEYS`), for an action on all of them."""

        if not quotation_ids:
            return []
        conditions = self._base(quotation_ids, board)
        if board.chip:
            conditions.append(_CHIP_CONDITIONS[board.chip])
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
        self, quotation_id: UUID, stock: dict[str, tuple[Decimal, Decimal | None]]
    ) -> None:
        """Store the stock the sync read for an update: ``(on hand, available)`` by SKU; its
        other SIESA items have none (0)."""

        table = L.__table__
        async with self.database.session_factory() as session, session.begin():
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
