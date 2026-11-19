"""The stock of a quotation follows the chosen operation centers (2026-11-19): consolidated
over their warehouses, with the detail by center and warehouse."""

from decimal import Decimal

import pytest

from app.core.errors import InvalidInputError
from app.modules.quotations.domain.entities import BillingCenter, StockRow
from app.modules.quotations.presentation.router import _stock_items
from tests.modules.quotations.conftest import NIT


def row(warehouse: str, center: str, on_hand: str, available: str | None = None) -> StockRow:
    return StockRow(
        "2537",
        warehouse,
        f"BODEGA {warehouse}",
        center,
        Decimal(on_hand),
        Decimal(available if available is not None else on_hand),
    )


@pytest.fixture
def stocked(warehouse):
    warehouse.centers += [BillingCenter("002", "OCCIDENTE"), BillingCenter("007", "URABA")]
    warehouse.stock_rows = [
        row("MDE1", "001", "80", "70"),
        row("MDE3", "001", "5"),
        # Obsolete warehouse of Medellín: never a sellable stock.
        row("MDE23", "001", "999"),
        row("OCC1", "002", "20"),
        row("BTA9", "003", "50"),
        row("APO1", "007", "7"),
    ]
    return warehouse


async def test_the_line_shows_the_stock_of_the_chosen_centers(service, advisor, stocked) -> None:
    created = await service.create(advisor, NIT, 12)
    # The customer bills from 001: Medellín's sellable warehouses only.
    assert created.quotation.cost_centers == ("001",)
    assert created.lines[0].stock_on_hand == Decimal(85)
    assert created.lines[0].stock_available == Decimal(75)

    both = await service.set_billing_center(advisor, created.quotation.id, 1, ["001", "002"])
    assert both.lines[0].stock_on_hand == Decimal(105)

    national = await service.set_billing_center(advisor, created.quotation.id, 2, [])
    # Nacional: every main warehouse (no obsolete, no Urabá).
    assert national.lines[0].stock_on_hand == Decimal(155)

    breakdown = await service.stock_breakdown(advisor, created.quotation.id)
    assert breakdown.scope.national and breakdown.skus == ("2537",)
    items = _stock_items(breakdown)
    assert items[0].on_hand == Decimal(155)
    assert [(center.code, center.on_hand) for center in items[0].centers] == [
        ("001", Decimal(85)),
        ("002", Decimal(20)),
        ("003", Decimal(50)),
    ]
    assert [item.warehouse for item in items[0].centers[0].warehouses] == ["MDE1", "MDE3"]


async def test_a_center_without_warehouses_reads_its_own(service, advisor, stocked) -> None:
    created = await service.create(advisor, NIT, 12)
    urab = await service.set_billing_center(advisor, created.quotation.id, 1, ["001", "007"])

    assert urab.lines[0].stock_on_hand == Decimal(92)
    breakdown = await service.stock_breakdown(advisor, created.quotation.id, ["2537", "9999"])
    assert breakdown.scope.centers == ("007",) and breakdown.skus == ("2537",)
    assert breakdown.totals["2537"].warehouses == 3


async def test_a_reference_without_stock_is_zero(service, advisor, warehouse) -> None:
    created = await service.create(advisor, NIT, 12)

    assert created.lines[0].stock_on_hand == Decimal(0)
    items = _stock_items(await service.stock_breakdown(advisor, created.quotation.id))
    assert items[0].on_hand == Decimal(0) and items[0].centers == []
    with pytest.raises(InvalidInputError, match="hasta 500"):
        await service.stock_breakdown(advisor, created.quotation.id, [str(n) for n in range(501)])
