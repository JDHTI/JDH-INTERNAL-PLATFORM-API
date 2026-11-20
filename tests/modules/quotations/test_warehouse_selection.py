"""Choosing main or spot-request warehouses inside each operation center (2026-11-20)."""

from datetime import date

import pytest

from app.core.errors import InvalidInputError
from app.modules.quotations.domain import pricing
from tests.modules.quotations.conftest import LIST_MANAGER, NIT, cost
from tests.modules.quotations.test_cost_centers import OUTSIDE_SKILL, national_customer
from tests.modules.supplier_supports.conftest import person

SNAPSHOT = {"snapshot_date": date(2026, 11, 3)}

# ------------------------------------------------------------------ domain


def test_a_center_with_chosen_warehouses_reads_only_those() -> None:
    plan = pricing.warehouse_plan("001", ["MDE1", "MDE3", "MDE4"])
    # Frozen and refrigerated lines read the cold room; the rest the other chosen ones.
    assert plan.reads() == (("MDE1", "MDE4"), ("MDE3",))
    assert plan.label == "Bodegas MDE1, MDE3, MDE4"
    # Without the cold room every line reads the chosen ones.
    spot = pricing.warehouse_plan("001", ["MDE4"])
    assert spot.cold_room is None and spot.reads() == (("MDE4",), ("MDE4",))
    # Only the cold room: every line reads it.
    assert pricing.warehouse_plan("003", ["BTA8"]).reads() == (("BTA8",), ("BTA8",))
    assert pricing.warehouse_plan("003", ["BTA20"]).lactalis_branch == "003"
    # The plans of today keep reading as before.
    assert pricing.stock_plan(OUTSIDE_SKILL, "001").reads() == (("MDE1",), ("MDE3",))
    general = pricing.stock_plan("1", None).reads()
    assert general == (pricing.GENERAL_WAREHOUSES, pricing.GENERAL_WAREHOUSES)


def test_chosen_warehouses_shape_the_plans_and_the_label() -> None:
    plans = pricing.stock_plans(OUTSIDE_SKILL, ["001", "003"], {"001": ("MDE4",)})
    assert plans["001"].selected == ("MDE4",)
    # A center without any chosen keeps its own warehouses.
    assert plans["003"].warehouses == ("BTA9", "BTA8") and not plans["003"].selected
    assert pricing.stock_label(plans) == (
        "Mayor costo entre C.O. 001: Bodegas MDE4;"
        " C.O. 003: BTA9 / BTA8 (congelados y refrigerados)"
    )
    # A list with its own warehouses: the chosen ones decide the cost of their center.
    skill = pricing.stock_plans("001", ["003"], {"003": ("BTA20",)})
    assert list(skill) == ["003"] and skill["003"].selected == ("BTA20",)
    # Warehouses of a center that is not chosen are ignored.
    assert pricing.stock_plans("001", ["003"], {"001": ("MDE4",)}) == pricing.stock_plans(
        "001", ["003"]
    )


def test_the_stock_counts_only_the_chosen_warehouses_of_a_center() -> None:
    scope = pricing.stock_scope(OUTSIDE_SKILL, ["001", "003"], {"001": ("MDE1", "MDE4")})
    assert scope.warehouses == ("MDE1", "MDE4", "BTA9", "BTA8", "BTA20")
    assert scope.label == "MDE1, MDE4, BTA9, BTA8, BTA20"
    # Nacional never narrows.
    assert pricing.stock_scope(OUTSIDE_SKILL, [], {"001": ("MDE4",)}).national


# ------------------------------------------------------------------ service


async def test_the_centers_list_carries_their_warehouses(service, advisor, warehouse) -> None:
    centers = {item.code: item for item in await service.billing_centers(advisor)}

    assert [item.code for item in centers["001"].warehouses] == ["MDE1", "MDE3", "MDE4"]
    assert centers["001"].warehouses[2].classification == "BSP"
    # When the warehouse master does not answer, the centers still come.
    warehouse.warehouses_down = True
    centers = await service.billing_centers(advisor)
    assert [item.code for item in centers] == ["001", "003", "004"]
    assert all(item.warehouses == () for item in centers)


async def test_chosen_warehouses_cost_and_stock_each_center(
    service, advisor, warehouse, masters
) -> None:
    national_customer(warehouse, masters)
    warehouse.plan_costs["MDE1,MDE4"] = {
        "2537": cost("2537", inventory="3400", **SNAPSHOT),
        "26278": cost("26278", inventory="900", **SNAPSHOT),
    }
    created = await service.create(advisor, NIT, 12)
    warehouse.calls.clear()

    changed = await service.set_billing_center(
        advisor, created.quotation.id, 1, ["003", "001"], ["mde4", "MDE1"]
    )

    quotation = changed.quotation
    assert quotation.cost_centers == ("001", "003")
    assert quotation.cost_warehouses == {"001": ("MDE1", "MDE4")}
    plans = [call[2] for call in warehouse.calls if call[0] == "costs"]
    assert [plan.selected for plan in plans] == [("MDE1", "MDE4"), ()]
    # Each line keeps the costliest center, now with 001 read from its chosen warehouses.
    by_sku = {line.sku: line for line in changed.lines}
    assert (by_sku["2537"].cost_center, by_sku["2537"].inventory_cost) == ("001", 3400)
    assert (by_sku["26278"].cost_center, by_sku["26278"].inventory_cost) == ("003", 1200)
    stock = [call for call in warehouse.calls if call[0] == "stock"]
    assert stock[-1][1] == ("MDE1", "MDE4", "BTA9", "BTA8", "BTA20")
    assert quotation.stock_source.startswith("Mayor costo entre C.O. 001: Bodegas MDE1, MDE4")

    # A refresh keeps the choice; choosing centers again without warehouses clears it.
    refreshed = await service.refresh(advisor, quotation.id, 2)
    assert refreshed.quotation.cost_warehouses == {"001": ("MDE1", "MDE4")}
    cleared = await service.set_billing_center(advisor, quotation.id, 3, ["001", "003"])
    assert cleared.quotation.cost_warehouses == {}


async def test_only_main_or_spot_warehouses_of_the_chosen_centers(
    service, advisor, warehouse, masters
) -> None:
    national_customer(warehouse, masters)
    quotation = (await service.create(advisor, NIT, 12)).quotation

    with pytest.raises(InvalidInputError, match="no pertenece"):
        await service.set_billing_center(advisor, quotation.id, 1, ["003"], ["MDE4"])
    with pytest.raises(InvalidInputError, match="no es principal ni de solicitudes"):
        await service.set_billing_center(advisor, quotation.id, 1, ["001"], ["MDE10"])
    with pytest.raises(InvalidInputError, match="no con Nacional"):
        await service.set_billing_center(advisor, quotation.id, 1, [], ["MDE4"])


async def test_comparing_centers_uses_their_chosen_warehouses(
    service, advisor, warehouse, masters
) -> None:
    national_customer(warehouse, masters)
    warehouse.plan_costs["MDE4"] = {
        "2537": cost("2537", inventory="9000", **SNAPSHOT),
        "26278": cost("26278", inventory="9000", **SNAPSHOT),
    }
    quotation = (await service.create(advisor, NIT, 12)).quotation

    assert await service.compare_centers(advisor, quotation.id, ["001", "003"]) == "003"
    assert await service.compare_centers(advisor, quotation.id, ["001", "003"], ["MDE4"]) == "001"


async def test_a_list_update_costs_from_the_chosen_warehouses(service, warehouse) -> None:
    """A list with its own warehouses (10U) costs from the chosen ones when there are."""

    lister = person(LIST_MANAGER)
    detail = await service.create_list_update(lister, price_list_id="10U", kind="lista")
    warehouse.calls.clear()

    changed = await service.set_billing_center(lister, detail.quotation.id, 1, ["003"], ["BTA20"])

    assert changed.quotation.cost_warehouses == {"003": ("BTA20",)}
    plans = [call[2] for call in warehouse.calls if call[0] == "costs"]
    assert [plan.selected for plan in plans] == [("BTA20",)]
    assert [call[1] for call in warehouse.calls if call[0] == "stock"][-1] == ("BTA20",)
