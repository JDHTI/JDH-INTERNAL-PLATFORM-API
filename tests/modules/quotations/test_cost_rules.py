"""The quoter takes its threshold and cost factors from Ajuste de costos, and a price set
from a margin never lands under it (2026-11-25)."""

from __future__ import annotations

from decimal import Decimal

from app.modules.quotations.application.service import LineChange
from app.modules.quotations.domain import rules
from tests.modules.quotations.conftest import NIT, branch


class FakeCostRules:
    def __init__(self, threshold="0.02", factors=None, fail=False) -> None:
        self.threshold = Decimal(threshold)
        self.factors = factors if factors is not None else {"generales": Decimal("1.01")}
        self.fail = fail

    async def threshold_and_factors(self):
        if self.fail:
            raise RuntimeError("base caída")
        return self.threshold, self.factors

    async def quote_rules(self, center):
        return ()


async def test_the_threshold_and_the_factor_come_from_the_module(service, advisor) -> None:
    service.cost_rules = FakeCostRules()

    created = await service.create(advisor, NIT, 12)

    first = created.lines[0]
    # Inventory 3,000 at the module's factor for Generales (1.01, not the code's 1.004).
    assert first.cost == Decimal("3030.0000") and first.cost_source == "inventario"
    # The minimum margin is the target minus the module's threshold (2 points).
    assert first.min_margin_pct == first.target_margin_pct - Decimal("0.02")
    assert created.quotation.max_points_below_target == Decimal("0.02")
    rules_page = await service.rule_catalog()
    cost_rule = next(
        rule for section in rules_page for rule in section.rules if rule.code == "costo_actual"
    )
    assert cost_rule.parameters["factor_costo"] == "1,01"


async def test_a_wholesale_customer_on_a_general_list_carries_no_factor(
    service, advisor, warehouse
) -> None:
    """2026-11-27: a customer with the Mayoristas margin is costed as Mayoristas whatever
    its list (here 001, Generales): no 0,4 %."""

    warehouse.branch_rows[NIT] = [
        branch(channel_code="0150", channel="MAYORISTAS Y DISTRIBUIDORES")
    ]

    created = await service.create(advisor, NIT, 12)

    assert created.lines[0].cost == Decimal("3000.0000")
    assert created.quotation.price_family == "mayoristas"


async def test_without_the_module_the_quoter_keeps_its_own(service, advisor) -> None:
    service.cost_rules = FakeCostRules(fail=True)

    created = await service.create(advisor, NIT, 12)

    assert created.lines[0].cost == Decimal("3012.0000")
    assert created.quotation.max_points_below_target == Decimal("0.01")


def test_a_price_from_a_margin_goes_up_so_the_margin_is_never_under_it() -> None:
    """Asked for 20 %: 1,001 / 0.8 = 1,251.25. To the nearest peso it was 1,251 (19.98 %, a
    hundredth under and an alert); up it is 1,252 (20.05 %)."""

    price = rules.price_at_margin(Decimal(1001), Decimal(0), Decimal("0.2"), "generales")

    assert price == Decimal(1252)
    assert (price - 1001) / price >= Decimal("0.2")
    # With a support the suggested price rounds up too.
    assert rules.supported_price(
        Decimal(1001), Decimal(50), Decimal("0.2"), "mayoristas"
    ) == Decimal(1202)


async def test_pricing_a_line_at_a_margin_reaches_that_margin(service, advisor) -> None:
    created = await service.create(advisor, NIT, 12)
    line = created.lines[0]

    detail, skipped = await service.update_lines(
        advisor, created.quotation.id, 1, [LineChange(line.sku, margin_pct=Decimal("0.2"))]
    )

    priced = next(item for item in detail.lines if item.sku == line.sku)
    assert not skipped and priced.new_price is not None
    assert priced.new_margin_with_support_pct >= Decimal("0.2")
