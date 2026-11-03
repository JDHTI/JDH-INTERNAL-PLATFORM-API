"""Pricing a quotation line: margins, supports and the general rule."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.modules.quotations.domain import rules
from app.modules.quotations.domain.entities import CostInfo, ListPrice, QuotationPolicy
from tests.modules.order_validation.conftest import agreement
from tests.modules.quotations.conftest import NIT, branch, product, sale

POLICY = QuotationPolicy(Decimal("0.01"))
DAY = date(2026, 11, 3)


def build(agreements=(), cost="3000", channel="HOTELES Y CLUBES", item=None, price="4000"):
    return rules.build_line(
        1,
        sale(price=price),
        nit=NIT,
        channel=channel,
        scope=None,
        product=item if item is not None else product(),
        supplier_name="LOCERIA",
        cost=CostInfo("2537", Decimal(cost), False) if cost else None,
        list_price=ListPrice("2537", Decimal("4568")),
        tax=None,
        agreements=list(agreements),
        policy=POLICY,
        day=DAY,
        line_id=uuid4(),
    )


def test_margin_without_and_with_support() -> None:
    assert rules.margin(Decimal(100), Decimal(80)) == Decimal("0.2")
    # (100 - (80 - 8)) / 100
    assert rules.margin(Decimal(100), Decimal(80), Decimal(8)) == Decimal("0.28")
    assert rules.margin(Decimal(0), Decimal(80)) is None
    assert rules.variation(Decimal(110), Decimal(100)) == Decimal("0.1")


def test_line_without_support_uses_list_margin_for_the_channel() -> None:
    line = build()

    assert line.target_margin_pct == Decimal("0.2") and line.margin_basis == "lista"
    assert line.min_margin_pct == Decimal("0.19")
    assert line.current_margin_pct == Decimal("0.25")
    # 3000 / (1 - 0.19)
    assert line.min_price == Decimal("3703.7038")
    assert line.support is None and line.problems == ()


def test_wholesale_channel_uses_wholesale_margin() -> None:
    line = build(channel="MAYORISTAS Y DISTRIBUIDORES")

    assert line.target_margin_pct == Decimal("0.12") and line.margin_basis == "mayoristas"


def test_one_point_below_the_target_still_meets_the_rule() -> None:
    line = build()

    exact = rules.reprice(line, Decimal("3703.7038"))
    below = rules.reprice(line, Decimal("3690"))

    assert exact.compliance == "cumple"
    assert below.compliance == "bajo_minimo"
    assert below.variation_pct == Decimal("-0.0775")
    assert rules.reprice(below, None).compliance is None
    assert rules.counts([exact, below, line]) == (3, 2, 1)


def test_support_with_money_raises_the_margin_and_its_target_rules() -> None:
    support = agreement(
        supplier_value=Decimal("0.10"), target_margin_pct=Decimal("0.25"), program="TACTICO"
    )

    line = build([support])

    assert line.support is not None and line.support.amount == Decimal("300")
    # (4000 - (3000 - 300)) / 4000
    assert line.current_margin_with_support_pct == Decimal("0.325")
    assert line.target_margin_pct == Decimal("0.25") and line.margin_basis == "apoyo"
    priced = rules.reprice(line, Decimal("3500"))
    assert priced.new_margin_with_support_pct == Decimal("0.228571")
    assert priced.compliance == "bajo_minimo"


def test_the_support_with_more_money_wins_and_others_are_listed() -> None:
    small = agreement(supplier_value=Decimal("0.05"))
    big = agreement(supplier_value=Decimal("0.08"), min_quantity=Decimal(10))
    review = agreement(supplier_value=Decimal("0.5"), status="pendiente_revision")
    store = agreement(supplier_value=Decimal("0.5"), warehouse="MDE1")
    expired = agreement(supplier_value=Decimal("0.5"), valid_to=date(2026, 1, 1))
    other_customer = agreement(supplier_value=Decimal("0.5"), customer_nit="1")

    line = build([small, big, review, store, expired, other_customer])

    assert line.support is not None and line.support.support_id == big.id
    assert line.support.conditions == "Cantidad mínima 10"
    reasons = {item.support_id: item.reason for item in line.support_candidates}
    assert set(reasons) == {small.id, review.id, store.id}
    assert set(line.problems) == {"apoyo_condicionado", "apoyo_en_revision", "apoyo_fuera_bodega"}


def test_margin_exception_only_sets_the_target() -> None:
    exception = agreement(calc_method="margen_minimo", min_margin_pct=Decimal("0.10"))

    line = build([exception])

    assert line.support is not None and line.support.amount == 0
    assert line.target_margin_pct == Decimal("0.1") and line.min_margin_pct == Decimal("0.09")


def test_missing_cost_or_margin_is_reported() -> None:
    no_cost = rules.reprice(build(cost=None), Decimal("4000"))
    no_margin = rules.reprice(build(item=product(margins={})), Decimal("4000"))

    assert no_cost.compliance == "sin_costo" and "sin_costo" in no_cost.problems
    assert no_cost.min_price is None and no_cost.current_margin_pct is None
    assert no_margin.compliance == "sin_objetivo" and "sin_margen_objetivo" in no_margin.problems


def test_customer_context_takes_the_majority_of_active_branches() -> None:
    context = rules.customer_context(
        NIT,
        [
            branch(),
            branch(branch_id="002", price_list_id="1M", billing_center_code="005"),
            branch(branch_id="003", price_list_id="1"),
            branch(branch_id="004", price_list_id="10U", is_active=False),
        ],
        28113,
    )

    assert context.price_list_id == "1" and context.billing_center_code == "001"
    assert set(context.warnings) == {"listas_distintas", "co_distintos", "lista_compartida"}


def test_customer_without_channel_or_list() -> None:
    context = rules.customer_context(
        NIT, [branch(channel_code=None, channel=None, price_list_id=None, is_active=False)]
    )

    assert context.price_list_id == "1"
    assert set(context.warnings) == {"cliente_inactivo", "sin_canal", "sin_lista"}


def test_price_with_taxes_adds_vat_and_value_tax() -> None:
    line = build()
    taxed = rules.reprice(line, Decimal("1000"))

    assert rules.price_with_taxes(taxed) == Decimal("1190.0000")
