"""A row of the main lists' board: the line with the list's stock in place of its own."""

from decimal import Decimal
from uuid import uuid4

from app.modules.quotations.application.list_review import StockTotal
from app.modules.quotations.domain.board import BoardRow
from app.modules.quotations.presentation.board_router import _row
from tests.modules.quotations.conftest import NIT


async def test_a_row_takes_the_list_stock_over_the_line_stock(service, advisor) -> None:
    quotation = (await service.create(advisor, NIT, 12)).quotation
    line = (await service.get(advisor, quotation.id)).lines[0]
    row = BoardRow(
        quotation_id=uuid4(),
        quotation_version=1,
        price_list_id="001",
        price_list_name="GENERAL",
        line=line,
    )

    with_stock = _row(row, StockTotal(on_hand=Decimal("40"), available=Decimal("35"), warehouses=2))
    without = _row(row)

    def stock(response):
        return response.stock_on_hand, response.stock_available, response.stock_warehouses

    assert stock(with_stock) == (Decimal("40"), Decimal("35"), 2)
    assert stock(without) == (None, None, 0)
    assert with_stock.sku == line.sku and with_stock.price_list_id == "001"
