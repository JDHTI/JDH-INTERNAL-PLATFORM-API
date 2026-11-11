"""A pending price increase in the cost, quotations that start empty, comparing centers."""

from decimal import Decimal

import pytest

from app.core.errors import InvalidInputError
from app.modules.quotations.domain import pricing
from tests.modules.quotations.conftest import NIT, cost
from tests.modules.quotations.test_cost_centers import national_customer

FACTOR = Decimal("1.004")


def test_a_pending_increase_wins_only_when_it_is_higher() -> None:
    assert pricing.effective_cost(Decimal(100), Decimal(90), FACTOR, Decimal(120)) == (
        Decimal("120.4800"),
        "alza",
    )
    # A tie keeps the quotation, then the inventory.
    assert pricing.effective_cost(Decimal(120), None, FACTOR, Decimal(120))[1] == "cotizacion"
    assert pricing.effective_cost(None, Decimal(120), FACTOR, Decimal(120))[1] == "inventario"
    assert pricing.effective_cost(None, None, FACTOR, Decimal(50))[1] == "alza"


async def test_the_quotation_takes_a_pending_increase(service, advisor, masters) -> None:
    masters.increases["2537"] = Decimal(3600)

    detail = await service.create(advisor, NIT, 12)

    line = detail.lines[0]
    assert line.cost_source == "alza" and line.increase_cost == Decimal(3600)
    assert line.cost == Decimal("3614.4000")

    masters.increases["2537"] = Decimal(2000)
    refreshed = await service.refresh(advisor, detail.quotation.id, 1)
    # Lower than the inventory (3000): it is shown but does not set the cost.
    assert refreshed.lines[0].cost_source == "inventario"
    assert refreshed.lines[0].increase_cost == Decimal(2000)


async def test_a_quotation_can_start_empty_and_stays_without_history(
    service, advisor, warehouse
) -> None:
    detail = await service.create(advisor, NIT, 12, with_history=False)

    assert detail.lines == () and not detail.quotation.with_history
    assert "sin_ventas" not in detail.quotation.warnings
    assert not [call for call in warehouse.calls if call[0] == "last_sales"]

    added, _ = await service.add_lines(advisor, detail.quotation.id, 1, ["26278"])
    refreshed = await service.refresh(advisor, detail.quotation.id, added.quotation.version)

    # The refresh revalidates the added reference and never brings the sales in.
    assert [line.sku for line in refreshed.lines] == ["26278"]
    assert not [call for call in warehouse.calls if call[0] == "last_sales"]


async def test_comparing_centers_names_the_costliest(service, advisor, warehouse, masters) -> None:
    national_customer(warehouse, masters)
    detail = await service.create(advisor, NIT, 12)
    quotation_id = detail.quotation.id

    # 001: 3200 + 1000 = 4200; 003: 3100 + 1200 = 4300.
    assert await service.compare_centers(advisor, quotation_id, ["001", "003"]) == "003"
    with pytest.raises(InvalidInputError, match="dos"):
        await service.compare_centers(advisor, quotation_id, ["001"])
    with pytest.raises(InvalidInputError, match="no existe"):
        await service.compare_centers(advisor, quotation_id, ["001", "999"])

    warehouse.plan_costs["BTA9"] = {
        "2537": cost("2537", inventory="3200"),
        "26278": cost("26278", inventory="1000"),
    }
    assert await service.compare_centers(advisor, quotation_id, ["001", "003"]) is None


def test_costliest_center_by_the_sum_of_unit_costs() -> None:
    costs = {
        "001": {"A": cost("A", "100"), "B": cost("B", "10"), "C": cost("C", "999")},
        "003": {"A": cost("A", "90"), "B": cost("B", "30")},
    }
    # C only has a cost in 001: it does not count. 001 = 110, 003 = 120.
    assert pricing.costliest_center(costs) == "003"
    assert pricing.costliest_center({"001": costs["001"]}) is None
