"""The SQL of the board and of the partial reads compiles for PostgreSQL and says what it
should (no database: the statements are captured)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.modules.quotations.domain import pricing
from app.modules.quotations.domain.board import BoardFilter
from app.modules.quotations.domain.entities import StockRow
from app.modules.quotations.infrastructure.board_repository import SqlAlchemyBoardRepository
from app.modules.quotations.infrastructure.repository import SqlAlchemyQuotationRepository


class _Result:
    rowcount = 1

    def one(self):
        # Every count is 0.
        return type("Counts", (), {"__getattr__": lambda self, name: 0})()

    def scalar_one(self):
        return 0

    def all(self):
        return []


class _Session:
    def __init__(self, statements: list) -> None:
        self.statements = statements

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def begin(self):
        return self

    async def execute(self, statement, params=None):
        self.statements.append((statement, params))
        return _Result()

    async def scalars(self, statement):
        self.statements.append((statement, None))
        return _Result()

    async def get(self, model, key):
        return object()


class _Database:
    def __init__(self) -> None:
        self.statements: list = []

    def session_factory(self):
        return _Session(self.statements)


def _sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


async def test_the_board_sorts_by_the_stored_stock_with_unread_rows_last() -> None:
    database = _Database()

    await SqlAlchemyBoardRepository(database).rows(
        [uuid4()], BoardFilter(sort="existencia", descending=True), 1, 100
    )

    page = _sql(database.statements[-1][0])
    assert "ORDER BY quotation_lines.stock_on_hand DESC NULLS LAST" in page


async def test_the_board_hides_rows_without_stock_and_small_rises_are_no_alza() -> None:
    """2026-11-23: a row the sync read at 0 is left out (unread stays); an «alza» needs at
    least 1 % more than the current price."""

    database = _Database()

    await SqlAlchemyBoardRepository(database).rows([uuid4()], BoardFilter(chip="alza"), 1, 100)

    counts, page = _sql(database.statements[0][0]), _sql(database.statements[-1][0])
    stock = "quotation_lines.stock_on_hand IS NULL OR quotation_lines.stock_on_hand >"
    assert stock in counts and stock in page
    rise = "coalesce(quotation_lines.new_price, quotation_lines.optimal_price) >="
    assert rise in page


async def test_the_stock_filter_can_be_turned_off() -> None:
    """2026-11-25: «Con existencia» is a quick filter, on by default; off, the rows the sync
    read at 0 show too, in the page and in the counts."""

    database = _Database()

    await SqlAlchemyBoardRepository(database).rows([uuid4()], BoardFilter(in_stock=False), 1, 100)
    await SqlAlchemyBoardRepository(database).keys([uuid4()], BoardFilter(in_stock=False))

    assert all("stock_on_hand >" not in _sql(statement) for statement, _ in database.statements)


async def test_the_stock_of_a_list_is_stored_with_its_breakdown_by_warehouse() -> None:
    """2026-11-26: the stock by warehouse is replaced in the same transaction as the totals;
    a warehouse at 0 is not stored."""

    database = _Database()
    quotation_id = uuid4()

    await SqlAlchemyBoardRepository(database).set_stock(
        quotation_id,
        {"2537": (Decimal(80), Decimal(80))},
        [
            StockRow("2537", "MDE1", "MEDELLIN", "001", Decimal(50), Decimal(50)),
            StockRow("2537", "MDE3", "CUARTO FRIO", "001", Decimal(30), Decimal(30)),
            StockRow("2537", "MDE4", None, "001", Decimal(0), Decimal(0)),
        ],
    )

    (dropped, _), (breakdown, rows) = database.statements[0], database.statements[1]
    assert _sql(dropped).startswith("DELETE FROM main_list_stock")
    assert "INSERT INTO main_list_stock" in _sql(breakdown)
    assert [(row["warehouse"], row["on_hand"]) for row in rows] == [
        ("MDE1", Decimal(50)),
        ("MDE3", Decimal(30)),
    ]
    reset, (stored, params) = database.statements[2][0], database.statements[3]
    assert "SET stock_on_hand=" in _sql(reset) and "origin !=" in _sql(reset)
    assert "WHERE quotation_lines.quotation_id = %(owner_id)s" in _sql(stored)
    assert params == [
        {
            "owner_id": quotation_id,
            "item_sku": "2537",
            "stock_on_hand": Decimal(80),
            "stock_available": Decimal(80),
        }
    ]


async def test_the_warehouse_filter_keeps_its_lists_and_their_stock_there() -> None:
    """2026-11-26: filtering by MDE1 keeps 001, 1M and 1 and, in stock, the rows with stock
    in MDE1; sorting by stock sums the chosen warehouses."""

    database = _Database()

    await SqlAlchemyBoardRepository(database).rows(
        [uuid4()], BoardFilter(warehouses=("MDE1",), sort="existencia", descending=True), 1, 100
    )
    await SqlAlchemyBoardRepository(database).rows(
        [uuid4()], BoardFilter(warehouses=("MDE1",), in_stock=False), 1, 100
    )

    page = database.statements[2][0]
    sql = _sql(page)
    assert "quotations.price_list_id IN (__[POSTCOMPILE_price_list_id_1])" in sql
    assert "EXISTS (SELECT * \nFROM main_list_stock" in sql
    assert "ORDER BY (SELECT sum(main_list_stock.on_hand)" in sql
    lists = page.compile(dialect=postgresql.dialect()).params["price_list_id_1"]
    assert sorted(lists) == ["001", "1", "1M"]
    without_stock = _sql(database.statements[-1][0])
    assert "main_list_stock" not in without_stock and "stock_on_hand >" not in without_stock


def test_the_main_lists_warehouses_are_bis_mapping() -> None:
    assert pricing.MAIN_LIST_WAREHOUSES["003"] == ("BTA8", "BTA9")
    assert pricing.MAIN_LIST_WAREHOUSES["10N"] == ("BGA1",)
    assert len(pricing.MAIN_LIST_WAREHOUSES["1"]) == 11
    # The board reads a main list in its own warehouses, whatever its saved filter of centers.
    assert pricing.main_list_scope("001", ["004"]).warehouses == ("MDE1", "MDE3")
    assert pricing.main_list_scope("1DR", []).warehouses == ("BAQ1", "BAQ2")
    # A list outside the mapping keeps the general rule.
    assert pricing.main_list_scope("777", []).national


async def test_a_partial_read_asks_only_for_its_skus(monkeypatch) -> None:
    database = _Database()
    repository = SqlAlchemyQuotationRepository(database)
    monkeypatch.setattr(
        "app.modules.quotations.infrastructure.repository._quotation", lambda model: model
    )

    await repository.get_lines(uuid4(), ["2537", "26278"])
    header_only = len(database.statements)
    await repository.get_lines(uuid4(), ())

    lines = _sql(database.statements[0][0])
    assert "quotation_lines.sku IN (__[POSTCOMPILE_sku_1])" in lines
    # Without SKUs only the header is read.
    assert len(database.statements) == header_only
