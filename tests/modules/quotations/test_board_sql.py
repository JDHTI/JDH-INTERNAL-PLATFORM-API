"""The SQL of the board and of the partial reads compiles for PostgreSQL and says what it
should (no database: the statements are captured)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.modules.quotations.domain.board import BoardFilter
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


async def test_the_stock_of_a_list_is_stored_in_two_statements() -> None:
    database = _Database()
    quotation_id = uuid4()

    await SqlAlchemyBoardRepository(database).set_stock(
        quotation_id, {"2537": (Decimal(80), Decimal(80))}
    )

    reset, (stored, params) = database.statements[0][0], database.statements[1]
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
