"""Pricing a quotation line: cost, margins, supports and the general rule."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.modules.quotations.domain import pricing, rules
from app.modules.quotations.domain.entities import (
    ItemInfo,
    ListPrice,
    QuotationPolicy,
    ValueTaxKey,
)
from tests.modules.order_validation.conftest import agreement
from tests.modules.quotations.conftest import NIT, branch, cost, product, sale

POLICY = QuotationPolicy(Decimal("0.01"))
DAY = date(2026, 11, 3)


def build(
    agreements=(),
    inventory="3000",
    channel="HOTELES Y CLUBES",
    item=None,
    price="4000",
    family=pricing.WHOLESALE,
    **values,
):
    """A line of SKU 2537 priced in a list family without cost factor (cost = inventory)."""

    return rules.build_line(
        1,
        "2537",
        values.pop("sale", sale(price=price)),
        nit=NIT,
        channel=channel,
        scope=None,
        product=item if item is not None else product(),
        supplier_name="LOCERIA",
        cost=values.pop("cost", cost(inventory=inventory) if inventory else None),
        list_price=ListPrice("2537", Decimal("4568")),
        tax=None,
        agreements=list(agreements),
        policy=POLICY,
        day=DAY,
        line_id=uuid4(),
        price_family=family,
        **values,
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
    # 3000 / (1 - 0.19) = 3703.70, up to whole pesos; optimal 3000 / 0.8.
    assert line.min_price == Decimal("3704") and line.optimal_price == Decimal("3750")
    assert line.support is None and line.problems == () and not line.below_target


def test_cost_is_the_highest_of_quotation_and_inventory_with_the_family_factor() -> None:
    general = build(family=pricing.GENERAL, cost=cost(inventory="3000.2", quote="2900"))
    quoted = build(cost=cost(inventory="1", quote="2640"))
    only_quote = build(cost=cost(quote="2640"))

    # ceil(3000.2) x 1.004
    assert general.cost == Decimal("3013.0040") and general.cost_source == "inventario"
    assert general.quote_cost == Decimal("2900") and general.inventory_cost == Decimal("3000.2")
    # A one-peso inventory cost is a placeholder: the quotation wins.
    assert quoted.cost == Decimal("2640") and quoted.cost_source == "cotizacion"
    assert only_quote.cost == Decimal("2640")
    assert pricing.effective_cost(Decimal(5), Decimal(5), Decimal(1)) == (Decimal(5), "cotizacion")
    assert pricing.effective_cost(None, Decimal("0.98"), Decimal(1)) == (None, None)


def test_a_discarded_quotation_is_flagged() -> None:
    line = build(cost=cost(inventory="2049", discarded_quote=Decimal("80552")))

    assert line.cost == Decimal("2049") and line.discarded_quote_cost == Decimal("80552")
    assert "cotizacion_descartada" in line.problems


def test_wholesale_channel_uses_wholesale_margin() -> None:
    line = build(channel="MAYORISTAS Y DISTRIBUIDORES")

    assert line.target_margin_pct == Decimal("0.12") and line.margin_basis == "mayoristas"


def test_one_point_below_the_target_still_meets_the_rule() -> None:
    line = build()

    exact = rules.reprice(line, Decimal("3704"))
    below = rules.reprice(line, Decimal("3690"))

    assert exact.compliance == "cumple"
    assert below.compliance == "bajo_minimo"
    assert below.variation_pct == Decimal("-0.0775")
    assert rules.reprice(below, None).compliance is None
    assert rules.counts([exact, below, line]) == (3, 2, 1, 0)


def test_price_for_a_margin_rounds_by_family_and_never_under_the_minimum() -> None:
    line = build()

    # 3000 / 0.7 = 4285.71
    assert rules.price_for_margin(line, Decimal("0.30"), pricing.WHOLESALE) == Decimal("4286")
    assert rules.price_for_margin(line, Decimal("0.30"), pricing.SALSAMENTARIAS) == Decimal("4286")
    # 3000 / 0.81 = 3703.70: nearest would be 3704 anyway; the floor guards the rounding down.
    assert rules.price_for_margin(line, Decimal("0.19"), pricing.WHOLESALE) == Decimal("3704")
    assert rules.price_for_margin(build(inventory=None), Decimal("0.3"), "x") is None
    assert pricing.round_price(Decimal("3750.2"), pricing.SALSAMENTARIAS) == Decimal("3751")
    assert pricing.round_price(Decimal("3750.2"), pricing.GENERAL) == Decimal("3750")


def test_last_price_under_the_minimum_is_flagged_unless_a_support_covers_it() -> None:
    low = build(price="3500")
    covered = build([agreement(supplier_value=Decimal("0.20"))], price="3500")

    assert low.below_target and low.current_margin_with_support_pct == Decimal("0.142857")
    # (3500 - (3000 - 600)) / 3500
    assert not covered.below_target
    assert covered.current_margin_with_support_pct == Decimal("0.314286")
    assert rules.counts([low, covered]) == (2, 0, 0, 1)


def test_inactive_items_are_marked_and_never_exported() -> None:
    inactive = rules.reprice(build(sale=sale(is_active=False)), Decimal("4000"))
    active = rules.reprice(build(), Decimal("4000"))

    assert not inactive.is_active and "referencia_inactiva" in inactive.problems
    assert rules.exported([inactive, active]) == [active]
    assert rules.counts([inactive, active])[1] == 1


def test_support_with_money_raises_the_margin_and_its_target_rules() -> None:
    support = agreement(
        supplier_value=Decimal("0.10"), target_margin_pct=Decimal("0.25"), program="TACTICO"
    )

    line = build([support])

    assert line.support is not None and line.support.amount == Decimal("300")
    # (4000 - (3000 - 300)) / 4000
    assert line.current_margin_with_support_pct == Decimal("0.325")
    assert line.target_margin_pct == Decimal("0.25") and line.margin_basis == "apoyo"
    # Precio apoyado sugerido = list price - support = 3000 / 0.75 - 300
    assert line.optimal_price == Decimal("3700")
    priced = rules.reprice(line, Decimal("3500"))
    assert priced.new_margin_with_support_pct == Decimal("0.228571")
    assert priced.compliance == "bajo_minimo"


def test_rules_v2_percent_on_the_list_price_takes_its_absolute_value() -> None:
    # Cost 3.000 at the list margin 20 %: list price 3.750; 10 % of it is 375 off the cost.
    line = build([agreement(calc_method="pct_precio_lista", supplier_value=Decimal("0.10"))])

    assert line.support.amount == Decimal("375")
    assert line.optimal_price == Decimal("3375")  # 3.750 - 375


def test_rules_v2_a_box_value_is_divided_by_the_pack_factor() -> None:
    boxed = agreement(
        calc_method="valor_unidad",
        supplier_value=Decimal("2400"),
        value_unit="caja",
        pack_factor=Decimal("12"),
    )

    line = build([boxed])

    assert line.support.amount == Decimal("200")  # 2.400 per box of 12


def test_rules_v2_jdh_authorized_margin_sets_the_minimum_price() -> None:
    supported = agreement(
        calc_method="costo_neto",
        supplier_value=Decimal("2700"),
        supported_margin_pct=Decimal("0.10"),
    )

    line = build([supported])

    assert line.support.authorized_margin_pct == Decimal("0.1")
    assert line.min_margin_pct == Decimal("0.1")
    assert line.min_price == Decimal("3000")  # $Costo apoyado 2.700 / (1 - 10 %)
    assert line.optimal_price == Decimal("3450")  # 3.750 - (3.000 - 2.700)


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
    no_cost = rules.reprice(build(inventory=None), Decimal("4000"))
    no_margin = rules.reprice(build(item=product(margins={})), Decimal("4000"))

    assert no_cost.compliance == "sin_costo" and "sin_costo" in no_cost.problems
    assert no_cost.min_price is None and no_cost.current_margin_pct is None
    assert no_cost.optimal_price is None
    assert no_margin.compliance == "sin_objetivo" and "sin_margen_objetivo" in no_margin.problems


def test_customer_context_takes_the_majority_of_active_branches() -> None:
    context = rules.customer_context(
        NIT,
        [
            branch(),
            branch(branch_id="002", price_list_id="1M", billing_center_code="005"),
            branch(branch_id="003", price_list_id="001"),
            branch(branch_id="004", price_list_id="10U", is_active=False),
        ],
        28113,
    )

    assert context.price_list_id == "001" and context.billing_center_code == "001"
    assert set(context.warnings) == {"listas_distintas", "co_distintos", "lista_compartida"}


def test_customer_without_channel_or_list() -> None:
    context = rules.customer_context(
        NIT, [branch(channel_code=None, channel=None, price_list_id=None, is_active=False)]
    )

    assert context.price_list_id == "1"
    assert set(context.warnings) == {"cliente_inactivo", "sin_canal", "sin_lista"}


def liquor(regional: str, list_price: ListPrice | None):
    return rules.build_line(
        1,
        "18973",
        None,
        item=ItemInfo("18973", "Juniper Moscow Mule", "UNID", Decimal(5), "359"),
        nit=NIT,
        channel="RESTAURANTES",
        scope=None,
        product=product("18973", liquor_tax=Decimal("4036"), liquor_tax_coast=Decimal("4144")),
        supplier_name=None,
        cost=cost("18973", inventory="9000"),
        list_price=list_price,
        tax=ValueTaxKey("18973", "0024", "LICORES NAL"),
        agreements=[],
        policy=POLICY,
        day=DAY,
        line_id=uuid4(),
        price_family=pricing.WHOLESALE,
        regional=regional,
    )


def test_liquor_tax_of_the_list_else_the_master_value_of_the_regional() -> None:
    in_list = liquor("costa", ListPrice("18973", Decimal("12141"), Decimal("4100")))
    national = liquor("nacional", None)
    coast = liquor("costa", None)

    assert in_list.value_tax_amount == Decimal("4100")
    assert national.value_tax_amount == Decimal("4036")
    assert coast.value_tax_amount == Decimal("4144") and coast.value_tax_key == "0024"
    assert coast.origin == "agregado" and coast.last_price is None
    # 9000 / 0.81 = 11111.11
    assert coast.min_price == Decimal("11112")
    plain = rules.reprice(build(), Decimal("1000"))
    # VAT is not shown: 1000 stays 1000; the liquor tax is added.
    assert rules.price_with_taxes(plain) == Decimal("1000.0000")
    assert rules.price_with_taxes(rules.reprice(coast, Decimal("12000"))) == Decimal("16144.0000")


def test_stock_plan_and_family_follow_the_skill() -> None:
    central = pricing.stock_plan("10U", "001")
    general = pricing.stock_plan("1", "003")
    by_center = pricing.stock_plan("31A", "003")
    unmapped = pricing.stock_plan("62Y", "008")

    assert central.warehouses == ("BTA9", "BTA8") and central.lactalis_branch == "003"
    assert general.highest and general.lactalis_branch == "001"
    assert by_center.warehouses == ("BTA9", "BTA8") and by_center.source == "co"
    assert unmapped.highest and unmapped.source == "general"
    assert pricing.family("1M") == pricing.WHOLESALE
    assert pricing.family("31A", "mayoristas") == pricing.WHOLESALE
    assert pricing.family("10X") == pricing.SALSAMENTARIAS
    assert pricing.family("31A") == pricing.GENERAL
    assert pricing.regional("004") == "costa" and pricing.regional("009") == "nacional"
