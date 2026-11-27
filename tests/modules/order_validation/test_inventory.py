"""Inventory by warehouse in the retained orders validator (2026-11-27): the warehouse's average
cost and stock, a free price at cost 0 or 1, a 100 % margin line released by stock, the invoice
discount and the date filter."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.core.errors import DependencyUnavailableError, InvalidInputError
from app.infrastructure.data_provider import DataProviderUnavailable
from app.modules.order_validation.domain import rules
from app.modules.order_validation.domain.entities import InventoryInfo, ProductInfo
from app.modules.order_validation.infrastructure.inventory import WarehouseInventorySource
from tests.modules.order_validation.conftest import line
from tests.modules.order_validation.test_order_file import HEADER, ROW, read, workbook

D = Decimal
TODAY = date(2026, 11, 27)
READY = "listo_para_liberar"
PENDING = "pendiente_validacion"


def stock(cost: str | None = "90000", on_hand: str | None = "12") -> InventoryInfo:
    return InventoryInfo(
        sku="18294",
        warehouse="CTG14",
        cost=None if cost is None else D(cost),
        stock=None if on_hand is None else D(on_hand),
        snapshot_date=TODAY,
    )


def decide(inventory: InventoryInfo | None = None, **values):
    return rules.decide(line(warehouse="CTG14", **values), [], None, None, TODAY, inventory)


# ------------------------------------------------------------------ the line


def test_a_line_carries_the_warehouse_cost_and_stock_it_was_validated_with() -> None:
    result = decide(stock())

    assert (result.inventory_cost, result.inventory_stock) == (D("90000"), D("12"))
    assert result.inventory_date == TODAY
    # A regular cost does not change the decision: no support, no margin to compare with.
    assert result.reason == "sin_acuerdo"


@pytest.mark.parametrize("cost", ["0", "1", "0.5"])
def test_a_cost_of_zero_or_one_leaves_the_price_free(cost: str) -> None:
    result = decide(stock(cost=cost), unit_price=D("5"), margin_pct=D("-3"))

    assert (result.status, result.reason) == (READY, "precio_libre")
    assert "precio libre" in result.detail and "CTG14" in result.detail


@pytest.mark.parametrize("cost", ["1.5", None])
def test_a_real_or_unknown_cost_is_checked_as_always(cost: str | None) -> None:
    assert decide(stock(cost=cost)).reason == "sin_acuerdo"
    assert decide(None).reason == "sin_acuerdo"


def test_a_free_price_line_still_owes_its_liquor_tax() -> None:
    product = ProductInfo("18294", {}, liquor_tax=D("6911"))

    result = rules.decide(line(warehouse="CTG14"), [], None, product, TODAY, stock(cost="1"))

    assert (result.status, result.reason) == (PENDING, "falta_impuesto_licor")


def test_a_full_margin_line_with_stock_is_releasable() -> None:
    result = decide(stock(cost=None, on_hand="40"), margin_pct=D("1"))

    assert (result.status, result.reason) == (READY, "margen_100_con_existencias")
    assert "40 unidades" in result.detail


@pytest.mark.parametrize(
    ("inventory", "said"),
    [
        (stock(cost=None, on_hand="0"), "sin existencias en la bodega CTG14"),
        (None, "sin inventario de la referencia en la bodega CTG14"),
    ],
)
def test_a_full_margin_line_without_stock_still_needs_a_person(inventory, said: str) -> None:
    result = decide(inventory, margin_pct=D("1"))

    assert (result.status, result.reason) == (PENDING, "margen_100")
    assert said in result.detail


def test_a_full_margin_line_at_cost_zero_without_stock_is_held_not_free() -> None:
    # Margin 100 % is decided by the stock: cost 0 does not free it.
    assert decide(stock(cost="0", on_hand="0"), margin_pct=D("1")).reason == "margen_100"


# ----------------------------------------------------------------- the order


def _pair(inventory: InventoryInfo | None = None, **values):
    item = line(warehouse="CTG14", **values)
    return item, rules.decide(item, [], None, None, TODAY, inventory)


def test_a_full_margin_line_with_stock_does_not_hold_its_order() -> None:
    ready = _pair(margin_pct=D("1"), row_number=2, inventory=stock(cost=None, on_hand="3"))
    held = _pair(margin_pct=D("1"), row_number=3, inventory=stock(cost=None, on_hand="0"))
    other = _pair(row_number=4, inventory=stock(cost="1"))

    assert rules.release_basis([ready, other], False, D(0)) == rules.RELEASE_LINES
    assert rules.release_basis([ready, held], False, D(0)) is None
    assert rules.order_alerts([ready]) == ()
    assert [alert.code for alert in rules.order_alerts([held])] == ["margen_100"]


def test_a_free_price_line_stays_out_of_the_order_margin() -> None:
    free = _pair(margin_pct=D("0.90"), row_number=2, inventory=stock(cost="1"))
    regular = _pair(margin_pct=D("0.10"), row_number=3)

    margin, _ = rules.order_margins([free, regular])

    assert margin == D("0.100000")


def test_the_file_cost_takes_the_discount_off_the_price() -> None:
    # A 2026-11-27 line: 74.924 with 15 % off at 5,1236 % margin was margined at 60.422.
    item = line(unit_price=D("74924"), discount_pct=D("0.15"), margin_pct=D("0.051236"))

    assert rules.file_cost(item) == D("60422.41")
    assert rules.file_cost(replace(item, margin_pct=D("1"))) is None
    assert rules.file_cost(replace(item, discount_pct=None)) == D("71085.19")


def test_a_cost_off_the_warehouse_average_is_flagged_without_holding() -> None:
    item = line(warehouse="CTG14", unit_price=D("74924"), margin_pct=D("0.051236"))
    result = rules.decide(item, [], None, None, TODAY, stock(cost="60422"))
    discounted = replace(item, discount_pct=D("0.15"))

    assert rules.cost_differs(item, result) is True
    assert "costo_distinto" in rules.line_alerts(item, result)
    assert rules.cost_differs(discounted, result) is False
    assert rules.cost_differs(item, replace(result, inventory_cost=None)) is False
    # Only informs: the order is decided as before.
    assert rules.release_basis([(item, result)], False, D(0)) is None
    assert result.reason == "sin_acuerdo"


def test_the_order_discount_is_weighted_by_value() -> None:
    lines = [
        line(unit_price=D("100"), quantity=D("3"), discount_pct=D("0.15")),
        line(unit_price=D("100"), quantity=D("1"), discount_pct=D("0.05")),
        line(unit_price=D("100"), quantity=D("9")),
    ]

    assert rules.order_discount(lines) == D("0.125000")
    assert rules.order_discount(lines[2:]) is None


# --------------------------------------------------------------- the file


def test_the_discount_column_is_read_in_points() -> None:
    (item,) = read("p.xlsx", workbook([*HEADER, "Descuento promedio"], [*ROW, 15]))
    (without,) = read("p.xlsx", workbook(HEADER, ROW))

    assert item.discount_pct == D("0.15")
    assert without.discount_pct is None


# ------------------------------------------------------------- the service


class FakeInventory:
    def __init__(self, items: dict[tuple[str, str], InventoryInfo]) -> None:
        self.items = items
        self.asked: list[tuple[str, str]] = []

    async def inventory(self, items):
        self.asked = list(items)
        return {key: value for key, value in self.items.items() if key in items}


def _rows(*values: tuple[str, int, date, str]) -> bytes:
    rows = []
    for document, sku, day, warehouse in values:
        row = list(ROW)
        row[1], row[2], row[8], row[9] = (
            datetime(day.year, day.month, day.day),
            document,
            warehouse,
            sku,
        )
        rows.append(row)
    return workbook(HEADER, *rows)


async def test_upload_reads_each_line_in_its_own_warehouse(service, manager) -> None:
    fake = FakeInventory({("18294", "CTG14"): stock(cost="1")})
    service.inventory = fake
    content = _rows(("PV-1", 18294, date(2026, 11, 20), "ctg14 "), ("PV-2", 28703, TODAY, "MDE1"))

    batch = await service.upload(manager, "p.xlsx", content)
    page = await service.orders(batch.id, sort="file")

    assert sorted(fake.asked) == [("18294", "CTG14"), ("28703", "MDE1")]
    first, second = page.items
    assert first.lines[0].result.reason == "precio_libre" and first.status == READY
    assert second.lines[0].result.inventory_cost is None


async def test_a_warehouse_that_cannot_be_read_stops_the_upload(service, manager) -> None:
    class Down:
        async def inventory(self, items):
            raise DependencyUnavailableError("sin DWH")

    service.inventory = Down()

    with pytest.raises(DependencyUnavailableError):
        await service.upload(manager, "p.xlsx", _rows(("PV-1", 18294, TODAY, "CTG14")))


async def test_orders_filter_by_a_date_range(service, manager) -> None:
    content = _rows(
        ("PV-1", 18294, date(2026, 11, 20), "CTG14"),
        ("PV-2", 18294, date(2026, 11, 25), "CTG14"),
        ("PV-3", 18294, date(2026, 11, 27), "CTG14"),
    )
    batch = await service.upload(manager, "p.xlsx", content)

    page = await service.orders(
        batch.id, date_from=date(2026, 11, 21), date_to=date(2026, 11, 27), sort="file"
    )
    since = await service.orders(batch.id, date_from=date(2026, 11, 26))

    assert [order.document for order in page.items] == ["PV-2", "PV-3"]
    assert sum(page.counts.values()) == 2
    assert (page.first_date, page.last_date) == (date(2026, 11, 20), TODAY)
    assert [order.document for order in since.items] == ["PV-3"]
    with pytest.raises(InvalidInputError):
        await service.orders(batch.id, date_from=TODAY, date_to=date(2026, 11, 1))


# ------------------------------------------------------- the warehouse reads


class Connection:
    def __init__(self, rows=None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.calls: list[tuple] = []

    async def fetch(self, query, *args, timeout=None, settings=None):
        self.calls.append((query, args, settings))
        if self.error:
            raise self.error
        return self.rows


async def test_the_source_reads_the_installation_average_of_each_warehouse() -> None:
    snapshot = datetime(2026, 11, 27, 11, 35, tzinfo=UTC)
    connection = Connection(
        [
            {
                "referencia": "18294",
                "bodega": "CTG14",
                "costo_prom_unit_ins": D("90000.5"),
                "existencia": D("12"),
                "foto_at": snapshot,
            },
            # Same SKU in a warehouse no line asked for: left out.
            {
                "referencia": "18294",
                "bodega": "MDE1",
                "costo_prom_unit_ins": D("1"),
                "existencia": D("0"),
                "foto_at": snapshot,
            },
        ]
    )

    found = await WarehouseInventorySource(connection).inventory(
        [("18294", "ctg14"), ("28703", "MDE1")]
    )

    query, args, settings = connection.calls[0]
    assert '"costo_prom_unit_ins"' in query and '"consumo"."inventario_fecha"' in query
    assert args == (["18294", "28703"], ["CTG14", "MDE1"]) and settings == {"jit": "off"}
    assert found == {
        ("18294", "CTG14"): InventoryInfo("18294", "CTG14", D("90000.5"), D("12"), TODAY)
    }


async def test_the_source_turns_a_warehouse_failure_into_503() -> None:
    connection = Connection(error=DataProviderUnavailable("down"))

    with pytest.raises(DependencyUnavailableError):
        await WarehouseInventorySource(connection).inventory([("18294", "CTG14")])
    assert await WarehouseInventorySource(connection).inventory([]) == {}
    with pytest.raises(ValueError):
        WarehouseInventorySource(connection, schema="consumo; drop")
