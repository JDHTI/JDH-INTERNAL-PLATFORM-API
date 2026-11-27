"""Opt-in: the trend and «sin precio» filters of the board on PostgreSQL (2026-11-25).

Runs with ``SUPPORT_REQUEST_TEST_DATABASE_URL`` (a scratch database at the head revision).
A main list is built with the real service and repository, then read with the board's SQL.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.infrastructure.database import Database
from app.modules.quotations.application.service import LineChange
from app.modules.quotations.domain.board import BoardFilter
from app.modules.quotations.domain.entities import ItemInfo, ListItem, ListPrice, StockRow
from app.modules.quotations.infrastructure.board_repository import SqlAlchemyBoardRepository
from app.modules.quotations.infrastructure.repository import SqlAlchemyQuotationRepository
from tests.modules.quotations.conftest import MAIN_MANAGER, cost, product
from tests.modules.supplier_supports.conftest import person

pytestmark = pytest.mark.skipif(
    not os.getenv("SUPPORT_REQUEST_TEST_DATABASE_URL"),
    reason="Dedicated PostgreSQL integration URL not set",
)


@pytest.fixture
async def database():
    found = Database(os.environ["SUPPORT_REQUEST_TEST_DATABASE_URL"])
    try:
        yield found
    finally:
        await found.dispose()


@pytest.fixture
def repository(database):
    # The quotation service of the conftest writes to the scratch database.
    return SqlAlchemyQuotationRepository(database)


def _item(sku: str, price: str | None) -> ListItem:
    info = ItemInfo(sku, f"Item {sku}", "UNID", Decimal(19), "359")
    if price is None:
        return ListItem(info, None, placeholder=True)
    return ListItem(info, Decimal(price))


async def test_the_trend_and_the_rows_without_a_list_price(
    service, warehouse, masters, database, repository
) -> None:
    for sku in ("90001", "90002", "90003"):
        masters.catalog[sku] = product(sku)
        warehouse.cost_rows[sku] = cost(sku, inventory="3000")
    # The list's current prices; 90003 keeps only a placeholder.
    warehouse.prices["90001"] = ListPrice("90001", Decimal(4568))
    warehouse.prices["90002"] = ListPrice("90002", Decimal(4568))
    warehouse.prices["90003"] = ListPrice("90003", Decimal(1))
    warehouse.list_item_rows["001"] = [
        _item("2537", "4568"),  # new price above: sube
        _item("90001", "4568"),  # below: baja
        _item("90002", "4568"),  # no new price: estable
        _item("90003", None),  # placeholder: sin precio
    ]
    manager = person(MAIN_MANAGER)
    detail = await service.create_list_update(manager, price_list_id="001", kind="principal")
    quotation_id = detail.quotation.id
    try:
        await service.update_lines(
            manager,
            quotation_id,
            1,
            [LineChange("2537", Decimal(5000)), LineChange("90001", Decimal(4000))],
        )
        board = SqlAlchemyBoardRepository(database)

        async def skus(**values) -> tuple[list[str], dict[str, int]]:
            rows, total, counts = await board.rows([quotation_id], BoardFilter(**values), 1, 50)
            assert total == len(rows)
            return sorted(row.line.sku for row in rows), counts

        # In stock, as the sync stores it (the board leaves out what has none).
        await board.set_stock(
            quotation_id,
            {sku: (Decimal(5), Decimal(5)) for sku in ("2537", "90001", "90002", "90003")},
        )
        # The rule of 2026-11-26: cost 820 at an 18 % minimum margin is a floor of 1000; a
        # current price under it goes up, otherwise down (the new price does not decide).
        async with database.session_factory() as session, session.begin():
            await session.execute(
                text(
                    "update quotation_lines set cost = 820, min_margin_pct = 0.18,"
                    " last_price = case sku when '2537' then 999 when '90001' then 1000"
                    " when '90002' then 1500 else last_price end"
                    " where quotation_id = :id"
                ),
                {"id": quotation_id},
            )
        assert (await skus())[0] == ["2537", "90001", "90002", "90003"]
        assert (await skus(trend="sube"))[0] == ["2537"]
        assert (await skus(trend="baja"))[0] == ["90001", "90002"]
        assert (await skus(chip="alza"))[0] == ["2537"]
        found, counts = await skus(without_list_price=True)
        assert found == ["90003"]
        # The trends and «sin precio» are one group: counted without any of them.
        assert {name: counts[name] for name in ("sube", "baja", "sin_precio")} == {
            "sube": 1,
            "baja": 2,
            "sin_precio": 1,
        }
        assert counts["todos"] == 1  # «sin precio» is on: only that row
        # Combined with a chip: under «validada» (none) every trend counts 0; under
        # «sin_marcar» (all four) each counts its rows.
        _, counts = await skus(chip="validada")
        assert (counts["sube"], counts["baja"], counts["sin_precio"]) == (0, 0, 0)
        _, counts = await skus(chip="sin_marcar", trend="sube")
        assert (counts["sube"], counts["baja"]) == (1, 2)
        assert counts["sin_marcar"] == 1  # the chip's count follows the trend
        assert (await skus(chip="sin_marcar", trend="baja"))[0] == ["90001", "90002"]
        keys = await board.keys([quotation_id], BoardFilter(trend="sube"))
        assert [key.sku for key in keys] == ["2537"]
        # «Con existencia» (on by default) leaves out what the sync read at 0; off, it shows.
        await board.set_stock(quotation_id, {"2537": (Decimal(5), Decimal(5))})
        assert (await skus())[0] == ["2537"]
        found, counts = await skus(in_stock=False)
        assert found == ["2537", "90001", "90002", "90003"] and counts["todos"] == 4

        # By warehouse (2026-11-26): the breakdown is stored with the totals; the filter
        # keeps the rows in stock in a chosen warehouse and sorts by their stock there.
        await board.set_stock(
            quotation_id,
            {"2537": (Decimal(8), Decimal(8)), "90001": (Decimal(5), None)},
            [
                StockRow("2537", "MDE1", "MEDELLIN", "001", Decimal(2), Decimal(2)),
                StockRow("2537", "MDE3", "CUARTO FRIO", "001", Decimal(6), Decimal(6)),
                StockRow("90001", "MDE1", "MEDELLIN", "001", Decimal(5), None),
            ],
        )
        assert (await skus(warehouses=("MDE3",)))[0] == ["2537"]
        rows, _, _ = await board.rows(
            [quotation_id],
            BoardFilter(warehouses=("MDE1",), sort="existencia", descending=True),
            1,
            50,
        )
        assert [row.line.sku for row in rows] == ["90001", "2537"]
        breakdown = {row.line.sku: row.stock_by_warehouse for row in rows}
        assert [item.warehouse for item in breakdown["2537"]] == ["MDE3", "MDE1"]
        # A warehouse of no list of the board's updates leaves nothing.
        assert (await skus(warehouses=("BAQ1",)))[0] == []
    finally:
        await repository.delete(quotation_id)
