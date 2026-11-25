"""Costing from Nacional or from one or several operation centers, and BI's lines workbook."""

import io
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from app.core.errors import ForbiddenError, InvalidInputError
from app.modules.quotations.domain import pricing, rules
from app.modules.quotations.domain.entities import Quotation, QuotationDetail
from app.modules.quotations.infrastructure.lines_workbook import (
    HEADER_SHEET,
    LINES_SHEET,
    QuotationLinesWorkbook,
)
from tests.modules.quotations.conftest import NIT, branch, cost, product, sale

OUTSIDE_SKILL = "13Q"


def national_customer(warehouse, masters) -> None:
    """A customer on its own list, delivered from Antioquia and Centro, buying two SKUs."""

    warehouse.branch_rows[NIT] = [
        branch(price_list_id=OUTSIDE_SKILL, billing_center_code="001"),
        branch(branch_id="002", price_list_id=OUTSIDE_SKILL, billing_center_code="003"),
    ]
    warehouse.sales[NIT] = [sale(), sale("26278", "2000")]
    masters.catalog["26278"] = product("26278")
    snapshot = {"snapshot_date": date(2026, 11, 3)}
    warehouse.plan_costs = {
        pricing.NATIONAL_SOURCE: {
            "2537": cost("2537", inventory="3500", **snapshot),
            "26278": cost("26278", inventory="1300", **snapshot),
        },
        "MDE1": {
            "2537": cost("2537", inventory="3200", **snapshot),
            "26278": cost("26278", inventory="1000", **snapshot),
        },
        "BTA9": {
            "2537": cost("2537", inventory="3100", **snapshot),
            "26278": cost("26278", inventory="1200", **snapshot),
        },
    }


# ------------------------------------------------------------------ domain


def test_each_sku_takes_the_costliest_center() -> None:
    costs = {
        "001": {"A": cost("A", inventory="100"), "B": cost("B", quote="50"), "C": cost("C")},
        "003": {"A": cost("A", inventory="90", quote="95"), "B": cost("B", inventory="70")},
    }

    result = pricing.highest_cost(costs)

    assert result["A"].center_code == "001" and result["A"].inventory_cost == Decimal(100)
    assert result["B"].center_code == "003" and result["B"].inventory_cost == Decimal(70)
    # No cost anywhere: the row stays, but no center set it.
    assert result["C"].center_code is None
    # A tie goes to the first center.
    tie = pricing.highest_cost({"001": {"A": cost("A", "5")}, "003": {"A": cost("A", "5")}})
    assert tie["A"].center_code == "001"
    # One plan without a center (Nacional, or a list with its own warehouses): unmarked.
    assert pricing.highest_cost({None: {"A": cost("A", "5")}})["A"].center_code is None


def test_a_center_without_stock_does_not_set_the_cost() -> None:
    """2026-11-19: only warehouses with stock give the inventory cost."""

    empty = {"inventory_stocked": False}
    costs = {
        "001": {
            "A": cost("A", inventory="100", **empty),  # 001 has no stock of A
            "B": cost("B", inventory="80", **empty),
        },
        "003": {
            "A": cost("A", inventory="90", inventory_stocked=True),
            "B": cost("B", inventory="70", **empty),
        },
    }

    result = pricing.highest_cost(costs)

    assert result["A"].center_code == "003" and result["A"].inventory_cost == Decimal(90)
    # No center has stock of B: their last average cost stays, the highest wins.
    assert result["B"].center_code == "001" and result["B"].inventory_cost == Decimal(80)
    # Comparing centers: 001's 100 without stock gives way to its quotation (85) < 90.
    compared = {
        "001": {"A": cost("A", inventory="100", quote="85", **empty)},
        "003": {"A": cost("A", inventory="90", inventory_stocked=True)},
    }
    assert pricing.costliest_center(compared) == "003"
    # The supplier's quotation is not a warehouse's: it stays.
    quoted = {
        "001": {"A": cost("A", inventory="100", quote="95", **empty)},
        "003": {"A": cost("A", inventory="90", inventory_stocked=True)},
    }
    assert pricing.highest_cost(quoted)["A"].center_code == "001"
    assert pricing.highest_cost(quoted)["A"].inventory_cost is None


def test_plans_by_list_and_centers() -> None:
    skill = pricing.stock_plans("001", ["003", "004"])
    assert list(skill) == [None] and skill[None].warehouses == ("MDE1", "MDE3")
    assert pricing.stock_plans("1", [])[None].label.startswith("Mayor costo")

    national = pricing.stock_plans(OUTSIDE_SKILL, [])
    assert list(national) == [None] and national[None].highest
    assert pricing.stock_label(national) == "Nacional: mayor costo entre todas las bodegas"

    several = pricing.stock_plans(OUTSIDE_SKILL, ["001", "003"])
    assert {code: plan.warehouses for code, plan in several.items()} == {
        "001": ("MDE1", "MDE3"),
        "003": ("BTA9", "BTA8"),
    }
    assert several["003"].center == "003"
    assert pricing.stock_label(several) == "Mayor costo entre los C.O. 001, 003"
    assert pricing.stock_label(pricing.stock_plans(OUTSIDE_SKILL, ["002"])) == (
        "OCC1 / OCC16 (congelados y refrigerados)"
    )


def test_the_coast_among_the_centers_sets_the_regional() -> None:
    assert pricing.regional_of(["001", "004"], "001") == "costa"
    assert pricing.regional_of(["001", "003"], "004") == "nacional"
    # Nacional keeps the regional of the customer's billing center.
    assert pricing.regional_of([], "004") == "costa"
    assert pricing.regional_of([], "001") == "nacional"


def test_a_customer_billed_from_several_centers_starts_nacional() -> None:
    several = [
        branch(price_list_id=OUTSIDE_SKILL, billing_center_code="001"),
        branch(price_list_id=OUTSIDE_SKILL, billing_center_code="003"),
    ]
    assert rules.customer_context(NIT, several).cost_centers == ()

    one = [branch(price_list_id=OUTSIDE_SKILL), branch(price_list_id=OUTSIDE_SKILL)]
    assert rules.customer_context(NIT, one).cost_centers == ("001",)

    # A list with its own warehouses is not costed by center: it keeps the billing center.
    skill = [branch(billing_center_code="001"), branch(billing_center_code="004")]
    assert rules.customer_context(NIT, skill).cost_centers == ("004",)


# ------------------------------------------------------------------ service


async def test_nacional_takes_the_highest_cost_of_every_warehouse(
    service, advisor, warehouse, masters
) -> None:
    national_customer(warehouse, masters)

    detail = await service.create(advisor, NIT, 12)

    quotation = detail.quotation
    assert quotation.cost_centers == () and quotation.billing_center_source == "auto"
    assert quotation.stock_source == "Nacional: mayor costo entre todas las bodegas"
    plans = [call[2] for call in warehouse.calls if call[0] == "costs"]
    assert len(plans) == 1 and plans[0].source == pricing.NATIONAL_SOURCE
    by_sku = {line.sku: line for line in detail.lines}
    assert by_sku["2537"].inventory_cost == Decimal(3500)
    assert by_sku["2537"].cost_center is None


async def test_several_centers_take_the_costliest_per_line(
    service, advisor, warehouse, masters
) -> None:
    national_customer(warehouse, masters)
    created = await service.create(advisor, NIT, 12)

    changed = await service.set_billing_center(advisor, created.quotation.id, 1, ["003", "001"])

    quotation = changed.quotation
    assert quotation.cost_centers == ("001", "003")
    assert quotation.billing_center_source == "manual" and quotation.regional == "nacional"
    assert quotation.stock_source == "Mayor costo entre los C.O. 001, 003"
    by_sku = {line.sku: line for line in changed.lines}
    assert (by_sku["2537"].cost_center, by_sku["2537"].inventory_cost) == ("001", Decimal(3200))
    assert (by_sku["26278"].cost_center, by_sku["26278"].inventory_cost) == ("003", Decimal(1200))

    refreshed = await service.refresh(advisor, quotation.id, 2)
    assert refreshed.quotation.cost_centers == ("001", "003")
    assert {line.sku: line.cost_center for line in refreshed.lines} == {
        "2537": "001",
        "26278": "003",
    }

    back = await service.set_billing_center(advisor, quotation.id, 3, [])
    assert back.quotation.cost_centers == ()
    assert {line.sku: line.inventory_cost for line in back.lines} == {
        "2537": Decimal(3500),
        "26278": Decimal(1300),
    }
    with pytest.raises(InvalidInputError, match="no existe"):
        await service.set_billing_center(advisor, quotation.id, 4, ["001", "999"])


async def test_bi_downloads_every_line_with_its_costs(
    service, advisor, approver, warehouse, masters
) -> None:
    national_customer(warehouse, masters)
    created = await service.create(advisor, NIT, 12)
    await service.set_billing_center(advisor, created.quotation.id, 1, ["001", "003"])

    with pytest.raises(ForbiddenError):
        await service.lines_file(advisor, created.quotation.id)
    result = await service.lines_file(approver, created.quotation.id)

    assert result.filename == "Plantilla HOTEL EL BOSQUE SAS 2026-10-09.xlsx"
    book = load_workbook(io.BytesIO(result.content), read_only=True)
    rows = list(book[LINES_SHEET].iter_rows(values_only=True))
    header = rows[0]
    for title in (
        "Óptimo",
        "Mínimo",
        "Costo más alto",
        "C.O. del costo",
        "Costo cotización del proveedor",
        "Costo inventario",
    ):
        assert title in header
    assert header.index("Óptimo") < header.index("Mínimo")
    # A read-only sheet drops the empty cells at the end of a row.
    by_sku = {row[0]: dict(zip(header, row, strict=False)) for row in rows[1:]}
    assert by_sku["26278"]["C.O. del costo"] == "003 · CENTRO"
    assert by_sku["26278"]["Costo inventario"] == 1200
    assert by_sku["2537"]["Costo tomado de"] == "Inventario"
    facts = dict(book[HEADER_SHEET].iter_rows(values_only=True))
    assert facts["C.O. del costo"] == "001 · ANTIOQUIA, 003 · CENTRO"
    assert facts["Cliente"] == "HOTEL EL BOSQUE SAS"


def test_the_lines_workbook_writes_the_largest_quotation(service) -> None:
    """Write-only mode: the most lines a quotation may have, in one streamed pass."""

    line = rules.build_line(
        1,
        "2537",
        sale(),
        nit=NIT,
        channel="HOTELES Y CLUBES",
        scope=None,
        product=product(),
        supplier_name="LOCERIA",
        cost=cost(inventory="3000"),
        list_price=None,
        tax=None,
        agreements=[],
        policy=service.policy,
        day=date(2026, 11, 4),
        line_id=uuid4(),
        price_family="generales",
        regional="nacional",
    )
    detail = QuotationDetail(_header(), tuple(line for _ in range(3000)))

    content = QuotationLinesWorkbook().write(detail, {})

    book = load_workbook(io.BytesIO(content), read_only=True)
    assert sum(1 for _ in book[LINES_SHEET].iter_rows(values_only=True)) == 3001


def _header() -> Quotation:
    now = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
    return Quotation(
        id=uuid4(),
        customer_nit=NIT,
        customer_name="HOTEL EL BOSQUE SAS",
        channel_code="0142",
        channel="HOTELES Y CLUBES",
        price_list_id=OUTSIDE_SKILL,
        price_list_name="SODEXO",
        price_list_customers=1,
        billing_center_code="001",
        salesperson_code=None,
        salesperson_name=None,
        window_months=12,
        status="borrador",
        activation_date=None,
        export_price_list_id=None,
        warnings=(),
        built_at=now,
        cost_cut_date=None,
        max_points_below_target=Decimal("0.01"),
        line_count=3000,
        changed_count=0,
        below_rule_count=0,
        version=1,
        created_by_id=uuid4(),
        created_by="asesor@jdh.com.co",
        created_at=now,
        updated_by="asesor@jdh.com.co",
        updated_at=now,
    )
