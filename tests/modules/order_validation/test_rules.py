"""Release decision of a retained line against the supports master."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.modules.order_validation.domain import rules
from app.modules.order_validation.domain.entities import CustomerScope, ProductInfo
from tests.modules.order_validation.conftest import agreement, line

TODAY = date(2026, 10, 9)
D = Decimal
# Isolates the threshold: an order never reaches a 100 % margin, so no tolerance applies.
NO_TOLERANCE = rules.OrderPolicy(tolerance_min_order_margin=D("1"))


def info(**margins) -> ProductInfo:
    return ProductInfo("18294", {f"margin_{name}": value for name, value in margins.items()})


def decide(agreements=(), customer=None, reference=None, product=None, **values):
    if product is None and reference is not None:
        product = info(list=reference)
    return rules.decide(line(**values), list(agreements), customer, product, TODAY)


def test_a_percentage_of_cost_brings_the_margin_up_to_the_target() -> None:
    # cost = 100000 x 0.90; the supplier pays 5 % of it: margin 10 % + 5 % x 90 % = 14,5 %.
    support = agreement(supplier_value=D("0.05"), target_margin_pct=D("0.14"))

    result = decide([support])

    assert result.status == "listo_para_liberar" and result.reason == "cumple_con_apoyo"
    assert result.support_id == support.id
    assert result.effective_margin_pct == D("0.145000")
    assert result.required_margin_pct == D("0.140000")
    assert "14,50 %" in result.detail and "14,00 %" in result.detail


def test_below_the_agreed_margin_stays_pending_with_the_gap() -> None:
    support = agreement(supplier_value=D("0.05"), target_margin_pct=D("0.15"))

    result = decide([support])

    assert result.status == "pendiente_validacion" and result.reason == "no_cumple_acuerdo"
    assert result.checks[0].outcome == "no_cumple" and "<" in result.detail


def test_net_cost_is_read_as_a_share_of_the_reference_cost() -> None:
    support = agreement(
        calc_method="costo_neto",
        supplier_value=D("90"),
        reference_cost=D("100"),
        target_margin_pct=D("0.19"),
    )

    result = decide([support])

    # 10 % off the cost: 10 % + 10 % x 90 % = 19 %.
    assert result.reason == "cumple_con_apoyo" and result.effective_margin_pct == D("0.190000")


def test_net_cost_without_reference_cannot_be_evaluated() -> None:
    support = agreement(calc_method="costo_neto", supplier_value=D("90"))

    result = decide([support])

    assert result.reason == "acuerdo_no_evaluable" and "costo de referencia" in result.detail


def test_the_required_margin_falls_back_to_the_supplier_list_margin() -> None:
    support = agreement(supplier_value=D("0.05"))

    assert decide([support], reference=D("0.14")).reason == "cumple_con_apoyo"
    assert decide([support]).reason == "acuerdo_no_evaluable"


def test_list_price_percentage_uses_the_references_or_the_order_price() -> None:
    with_refs = agreement(
        calc_method="pct_precio_lista",
        supplier_value=D("0.05"),
        reference_cost=D("80"),
        reference_list_price=D("100"),
        target_margin_pct=D("0.15"),
    )
    without = agreement(
        calc_method="pct_precio_lista", supplier_value=D("0.05"), target_margin_pct=D("0.14")
    )

    # 5 % of a list 1,25x the cost is 6,25 % of the cost: 10 % + 6,25 % x 90 % = 15,625 %.
    assert decide([with_refs]).effective_margin_pct == D("0.156250")
    approximated = decide([without])
    assert approximated.effective_margin_pct == D("0.145000")
    assert "aproximado" in approximated.detail


def test_minimum_margin_exception_and_margin_points() -> None:
    exception = agreement(
        kind="excepcion_margen", calc_method="margen_minimo", min_margin_pct=D("0.08")
    )
    points = agreement(
        calc_method="puntos_margen", supplier_value=D("0.10"), list_margin_pct=D("0.18")
    )
    strict = agreement(
        kind="excepcion_margen", calc_method="margen_minimo", min_margin_pct=D("0.12")
    )

    assert decide([exception]).reason == "cumple_con_apoyo"
    assert decide([points]).effective_margin_pct == D("0.100000")
    assert decide([points]).required_margin_pct == D("0.080000")
    assert decide([strict]).reason == "no_cumple_acuerdo"


def test_rebates_over_sales_are_not_evaluable_per_order() -> None:
    rebate = agreement(kind="rebate", calc_method="pct_ventas", supplier_value=D("0.02"))

    result = decide([rebate])

    assert result.reason == "acuerdo_no_evaluable" and "periodo" in result.detail


def test_an_agreement_under_review_does_not_release_the_line() -> None:
    review = agreement(
        status="pendiente_revision", supplier_value=D("0.05"), target_margin_pct=D("0.14")
    )

    result = decide([review])

    assert result.status == "pendiente_validacion" and result.reason == "acuerdo_en_revision"
    assert result.checks[0].needs_review is True


def test_an_active_agreement_wins_over_one_under_review_and_the_best_is_kept() -> None:
    review = agreement(
        status="pendiente_revision", supplier_value=D("0.30"), target_margin_pct=D("0.14")
    )
    small = agreement(supplier_value=D("0.05"), target_margin_pct=D("0.14"))
    large = agreement(supplier_value=D("0.10"), target_margin_pct=D("0.14"))

    result = decide([review, small, large])

    assert result.reason == "cumple_con_apoyo" and result.support_id == large.id
    assert len(result.checks) == 3


def test_margin_at_or_above_the_list_margin_needs_no_agreement() -> None:
    result = decide(reference=D("0.10"))

    assert result.status == "listo_para_liberar" and result.reason == "cumple_margen_lista"


def test_no_agreement_and_low_margin_is_pending() -> None:
    other_client = agreement(customer_nit="800116299", supplier_value=D("0.5"))

    result = decide([other_client], reference=D("0.18"))

    assert result.reason == "sin_acuerdo" and "18,00 %" in result.detail
    assert result.checks == ()


def test_an_agreement_out_of_its_dates_is_reported() -> None:
    expired = agreement(
        supplier_value=D("0.5"), valid_from=date(2026, 1, 1), valid_to=date(2026, 6, 30)
    )

    result = decide([expired])

    assert result.reason == "acuerdo_vencido" and result.support_id == expired.id
    assert "2026-06-30" in result.detail


def test_the_order_date_decides_validity_not_today() -> None:
    september = agreement(
        supplier_value=D("0.05"),
        target_margin_pct=D("0.14"),
        valid_from=date(2026, 9, 1),
        valid_to=date(2026, 9, 30),
    )

    assert decide([september], order_date=date(2026, 9, 15)).reason == "cumple_con_apoyo"
    assert decide([september]).reason == "acuerdo_vencido"


def test_channel_regional_and_business_unit_scope() -> None:
    restaurants = agreement(
        channels=("RESTAURANTE",), supplier_value=D("0.05"), target_margin_pct=D("0.14")
    )
    west = agreement(
        regionals=("OCCIDENTE",), supplier_value=D("0.05"), target_margin_pct=D("0.14")
    )
    food = agreement(
        business_units=("FOOD SERVICE",), supplier_value=D("0.05"), target_margin_pct=D("0.14")
    )
    client = CustomerScope(
        "900315138", regionals=frozenset({"OCCIDENTE"}), business_units=frozenset({"RETAIL"})
    )

    # The support says RESTAURANTE, the file says RESTAURANTES.
    assert decide([restaurants], channel="RESTAURANTES").reason == "cumple_con_apoyo"
    assert decide([restaurants]).reason == "sin_acuerdo"
    assert decide([west], customer=client).reason == "cumple_con_apoyo"
    # An unknown client cannot be placed in a regional.
    assert decide([west]).reason == "sin_acuerdo"
    assert decide([food], customer=client).reason == "sin_acuerdo"


def test_client_sku_and_warehouse_scope() -> None:
    support = agreement(
        customer_nit="900315138",
        product_sku="18294",
        warehouse="CTG14",
        supplier_value=D("0.05"),
        target_margin_pct=D("0.14"),
    )

    assert decide([support], warehouse="ctg14").reason == "cumple_con_apoyo"
    assert decide([support], warehouse="MDE1").reason == "sin_acuerdo"
    assert decide([support], warehouse="CTG14", sku="999").reason == "sin_acuerdo"


def test_minimum_quantity_is_a_condition_of_the_agreement() -> None:
    support = agreement(min_quantity=D("20"), supplier_value=D("0.05"), target_margin_pct=D("0.14"))

    result = decide([support])

    assert result.reason == "no_cumple_acuerdo" and "mínima" in result.detail


def test_missing_data_is_pending_and_lists_what_could_not_be_read() -> None:
    result = decide(margin_pct=None, problems=("Margen ilegible: 'n/a'",))

    assert result.reason == "datos_incompletos"
    assert "margen" in result.detail and "ilegible" in result.detail


def test_a_full_margin_is_a_line_without_cost_not_a_release() -> None:
    result = decide(margin_pct=D("1"), reference=D("0.18"))

    assert result.status == "pendiente_validacion" and result.reason == "margen_100"
    assert "no trae costo" in result.detail and "Margen 100,00 %, revisar" in result.detail


def test_order_status_and_batch_counts() -> None:
    ready, pending = "listo_para_liberar", "pendiente_validacion"

    assert rules.order_status([ready, ready]) == ready
    assert rules.order_status([ready, pending]) == pending
    items = [
        (line(document=document), rules.LineResult(status, "sin_acuerdo", ""))
        for document, status in (("A", ready), ("A", pending), ("B", ready))
    ]
    assert rules.summarize_batch(items) == (3, 2, 2, 1, 0)
    # A person's mark releases the order (counted apart); its lines keep their status.
    assert rules.summarize_batch(items, {"A"}) == (3, 2, 2, 1, 1)
    # A ready order marked by hand leaves the ready count for the released one.
    assert rules.summarize_batch(items, {"B"}) == (3, 2, 2, 0, 1)


def test_the_margin_of_the_channel_is_the_reference() -> None:
    product = info(list=D("0.18"), wholesale=D("0.12"), salsamentarias=D("0.10"))

    assert decide(product=product, channel="HOTELES Y CLUBES").required_margin_pct == D("0.18")
    wholesale = decide(product=product, channel="MAYORISTAS Y DISTRIBUIDORES")
    assert wholesale.required_margin_pct == D("0.12") and wholesale.margin_basis == "mayoristas"
    assert decide(product=product, channel="SUBDISTRIBUIDOR").margin_basis == "mayoristas"
    # Salsamentarias is on the Mayoristas list; its own column is no longer applied.
    sal = decide(product=product, channel="SALSAMENTARIAS", margin_pct=D("0.12"))
    assert sal.reason == "cumple_margen_lista" and sal.margin_basis == "mayoristas"
    assert sal.detail == "Margen del pedido 12,00 % ≥ margen mayoristas 12,00 %"
    # Without a known client, a channel alone never reaches Masivo Food.
    other = ProductInfo("1", {"margin_list": D("0.30"), "margin_mass_food": D("0.01")})
    assert decide(product=other, channel="AUTOSERVICIO").required_margin_pct == D("0.30")


def test_a_promotional_warehouse_needs_a_support_that_says_so() -> None:
    day = date(2026, 11, 3)
    promo_line = line(warehouse="MDE27")  # group 23 PROMOCIONES

    assert rules.scope_of(agreement(), promo_line, None, day) == rules.OUT_OF_SCOPE
    assert rules.scope_of(agreement(promo_warehouses=True), promo_line, None, day) == rules.APPLIES
    assert rules.scope_of(agreement(), line(warehouse="MDE1"), None, day) == rules.APPLIES


def test_group_and_branch_supports_need_the_customer_in_them() -> None:
    goddard = CustomerScope(
        "900315138", business_group="GRUPO GODDARD", branches=frozenset({"001", "002"})
    )
    other = CustomerScope("900315138", business_group="OTRO")
    day = date(2026, 11, 3)

    group = agreement(customer_group="Grupo Goddard")
    branch = agreement(customer_nit="900315138", customer_branch_id="002")
    assert rules.scope_of(group, line(), goddard, day) == rules.APPLIES
    assert rules.scope_of(group, line(), other, day) == rules.OUT_OF_SCOPE
    assert rules.scope_of(group, line(), None, day) == rules.OUT_OF_SCOPE
    assert rules.scope_of(branch, line(), goddard, day) == rules.APPLIES
    assert rules.scope_of(branch, line(), other, day) == rules.OUT_OF_SCOPE


def test_the_client_margin_list_comes_from_its_center_and_team() -> None:
    product = ProductInfo(
        "1",
        {
            "margin_list": D("0.30"),
            "margin_wholesale": D("0.12"),
            "margin_mass_food": D("0.22"),
            "margin_market": None,
        },
    )
    mass = CustomerScope("1", margin_field="margin_mass_food")
    market = CustomerScope("2", margin_field="margin_market")

    result = decide(product=product, customer=mass, channel="AUTOSERVICIO", margin_pct=D("0.25"))
    assert result.margin_basis == "masivo_food" and result.required_margin_pct == D("0.22")
    # Market Apartadó without a margin of its own falls back to Food service.
    fallback = decide(product=product, customer=market, channel=None, margin_pct=D("0.31"))
    assert fallback.margin_basis == "market" and fallback.required_margin_pct == D("0.30")


def test_the_client_channel_is_used_when_the_file_has_none() -> None:
    product = info(list=D("0.18"), wholesale=D("0.12"))
    client = CustomerScope("900315138", channels=frozenset({"MAYORISTAS Y DISTRIBUIDORES"}))

    result = decide(product=product, customer=client, channel=None, margin_pct=D("0.12"))

    assert result.reason == "cumple_margen_lista" and result.margin_basis == "mayoristas"


def test_a_product_without_margin_for_the_channel_says_so() -> None:
    result = decide(product=info(wholesale=D("0.12")), channel="HOTELES Y CLUBES")

    assert result.reason == "sin_acuerdo"
    assert "no tiene margen definido para este canal" in result.detail
    assert result.margin_basis is None and result.required_margin_pct is None


def pair(price, quantity, margin, required, reason="sin_acuerdo", document="PV-1", row=2):
    item = line(
        document=document,
        unit_price=D(price),
        quantity=D(quantity),
        margin_pct=margin,
        row_number=row,
    )
    status = "listo_para_liberar" if reason.startswith("cumple") else "pendiente_validacion"
    return item, rules.LineResult(status, reason, "", required_margin_pct=required)


def test_the_threshold_acts_on_the_margin_of_the_whole_order() -> None:
    # 1 000 at 17 % and 3 000 at 20 %: the order margin is 19,25 % against a 20 % target.
    items = [
        pair("1000", "1", D("0.17"), D("0.20")),
        pair("3000", "1", D("0.20"), D("0.20"), "cumple_margen_lista"),
    ]

    assert rules.order_margins(items) == (D("0.192500"), D("0.200000"))
    # Without the tolerance (an order at 18 % or more with up to 2 short lines), only the
    # threshold releases it.
    assert not rules.release_by_margin(items, D("0"), NO_TOLERANCE)
    assert not rules.release_by_margin(items, D("0.007"), NO_TOLERANCE)
    assert rules.release_by_margin(items, D("0.0075"), NO_TOLERANCE) == "margen"
    assert rules.release_by_margin(items, D("0.03"), NO_TOLERANCE) == "margen"
    assert rules.release_basis(items, False, D("0.03"), NO_TOLERANCE) == "margen"
    assert rules.release_basis(items, False, D("0"), NO_TOLERANCE) is None
    assert rules.release_basis(items, True, D("0"), NO_TOLERANCE) == "manual"
    # With it, the 19,25 % order passes even with no threshold: the margin rule goes first.
    assert rules.release_basis(items, False, D("0")) == "tolerancia"
    assert rules.release_basis(items, False, D("0.03")) == "margen"


def test_the_threshold_example_of_the_business_19_5_against_20() -> None:
    items = [pair("100", "10", D("0.195"), D("0.20"), "no_cumple_acuerdo")]

    assert not rules.release_by_margin(items, D("0"), NO_TOLERANCE)
    assert rules.release_by_margin(items, D("0.005"), NO_TOLERANCE)
    assert not rules.release_by_margin(
        [pair("100", "10", D("0.194"), D("0.20"))], D("0.005"), NO_TOLERANCE
    )


def test_the_threshold_does_not_rescue_orders_that_need_a_person() -> None:
    short = pair("1000", "1", D("0.199"), D("0.20"))
    for reason in (
        "acuerdo_en_revision",
        "acuerdo_no_evaluable",
        "acuerdo_vencido",
        "datos_incompletos",
    ):
        other = pair("1000", "1", D("0.30"), D("0.20"), reason, row=3)
        assert not rules.release_by_margin([short, other], D("0.05"))
    # A line with nothing to compare with, or with no cost, keeps the order pending too.
    assert not rules.release_by_margin(
        [short, pair("1000", "1", D("0.30"), None, row=3)], D("0.05")
    )
    assert not rules.release_by_margin(
        [short, pair("1000", "1", D("1"), D("0.20"), row=3)], D("0.05")
    )
    assert rules.release_by_margin(
        [short, pair("1000", "1", D("0.30"), D("0.20"), "cumple_margen_lista", row=3)], D("0.05")
    )


def test_a_line_is_never_released_by_the_threshold() -> None:
    result = decide(reference=D("0.20"), margin_pct=D("0.195"))

    assert result.status == "pendiente_validacion" and result.reason == "sin_acuerdo"
    support = agreement(supplier_value=D("0.05"), target_margin_pct=D("0.1455"))
    assert decide([support]).reason == "no_cumple_acuerdo"


def test_a_line_that_meets_its_margin_but_lacks_the_liquor_tax_is_held_back() -> None:
    taxed = ProductInfo("18294", {"margin_list": D("0.20")}, liquor_tax=D("6911"))

    held = decide(product=taxed, margin_pct=D("0.25"))
    pending = decide(product=taxed, margin_pct=D("0.05"))
    carried = decide(product=taxed, margin_pct=D("0.25"), liquor_tax_value=D("6911"))

    # It meets the margin, yet the missing tax holds it back.
    assert held.status == "pendiente_validacion" and held.reason == "falta_impuesto_licor"
    assert held.liquor_tax and held.liquor_tax_amount == D("6911")
    assert "$6.911 por unidad" in held.detail and "≥ margen Food Service" in held.detail
    assert held.required_margin_pct == D("0.200000")
    # A line already pending for another reason keeps that reason, still flagged.
    assert pending.reason == "sin_acuerdo" and pending.liquor_tax
    # With the tax in the line it is decided like any other.
    assert carried.status == "listo_para_liberar" and not carried.liquor_tax
    assert not decide(product=taxed, margin_pct=D("0.25"), liquor_tax_value=D("0")).status == (
        "listo_para_liberar"
    )


def test_the_order_margin_is_weighted_by_amount() -> None:
    def pair(price, quantity, margin, required=None):
        item = line(unit_price=D(price), quantity=D(quantity), margin_pct=margin)
        return item, rules.LineResult(
            "pendiente_validacion", "sin_acuerdo", "", required_margin_pct=required
        )

    margin, required = rules.order_margins(
        [
            pair("100", "1", D("0.10"), D("0.20")),
            pair("300", "1", D("0.20"), D("0.20")),
            # No cost (100 %), unreadable margin and zero amounts stay out.
            pair("500", "1", D("1")),
            pair("100", "1", None),
            pair("0", "1", D("0.5")),
        ]
    )

    assert margin == D("0.175000") and required == D("0.200000")
    assert rules.order_margins([pair("100", "1", None)]) == (None, None)


def test_lines_without_support_come_first_then_lower_margins() -> None:
    def key(row, margin, reason):
        item = line(row_number=row, margin_pct=margin)
        return rules.line_sort_key(item, rules.LineResult("pendiente_validacion", reason, ""))

    ordered = sorted(
        [
            (3, key(3, D("0.30"), "cumple_con_apoyo")),
            (4, key(4, None, "sin_acuerdo")),
            (2, key(2, D("0.10"), "cumple_con_apoyo")),
            (5, key(5, D("0.25"), "sin_acuerdo")),
            (6, key(6, D("-0.05"), "sin_acuerdo")),
        ],
        key=lambda pair: pair[1],
    )

    assert [row for row, _ in ordered] == [6, 5, 4, 2, 3]


def test_the_liquor_flag_shows_only_when_the_order_lacks_the_tax() -> None:
    taxed = ProductInfo("12013", {"margin_list": D("0.57")}, liquor_tax=D("6911"))

    missing_zero = decide(product=taxed, liquor_tax_value=D("0"))
    missing_blank = decide(product=taxed, liquor_tax_value=None)
    carried = decide(product=taxed, liquor_tax_value=D("6911"))
    untaxed = decide(product=info(list=D("0.2")), liquor_tax_value=D("0"))

    assert missing_zero.liquor_tax and missing_zero.liquor_tax_amount == D("6911")
    assert missing_blank.liquor_tax
    # The order already carries the tax: nothing to flag, and no amount to show.
    assert not carried.liquor_tax and carried.liquor_tax_amount is None
    assert not untaxed.liquor_tax


def test_the_threshold_never_releases_an_order_with_a_line_missing_the_liquor_tax() -> None:
    short = pair("1000", "1", D("0.199"), D("0.20"))
    held = pair("1000", "1", D("0.30"), D("0.20"), "falta_impuesto_licor", row=3)
    held_result = replace(held[1], liquor_tax=True)
    items = [short, (held[0], held_result)]

    assert not rules.release_by_margin(items, D("0.05"))
    assert rules.release_basis(items, False, D("0.05")) is None
    # A pending line that is short on margin and also lacks the tax blocks the order too.
    short_taxed = (short[0], replace(short[1], liquor_tax=True))
    assert not rules.release_by_margin([short_taxed], D("0.05"))
    # A manual approval can still release it.
    assert rules.release_basis(items, True, D("0")) == "manual"


def test_only_the_threshold_can_release_an_order_whose_margin_falls_short() -> None:
    assert rules.needs_threshold(D("0.195"), D("0.20"))
    assert not rules.needs_threshold(D("0.20"), D("0.20"))
    assert not rules.needs_threshold(D("0.25"), D("0.20"))
    assert not rules.needs_threshold(None, D("0.20"))
    assert not rules.needs_threshold(D("0.20"), None)


def supported(price, quantity, file_margin, with_support, required, met=True, row=2, **check):
    """A line decided by a support of cost that brings it from its file margin to another."""

    item = line(unit_price=D(price), quantity=D(quantity), margin_pct=file_margin, row_number=row)
    support = agreement()
    outcome = rules.AgreementCheck(
        support_id=support.id,
        kind="tactico_cliente",
        calc_method=check.pop("calc_method", "pct_costo"),
        program=None,
        valid_from=None,
        valid_to=None,
        needs_review=check.pop("needs_review", False),
        outcome="cumple" if met else "no_cumple",
        detail="",
        effective_margin_pct=with_support,
        required_margin_pct=required,
    )
    result = rules.LineResult(
        "listo_para_liberar" if met else "pendiente_validacion",
        "cumple_con_apoyo" if met else "no_cumple_acuerdo",
        "",
        support_id=support.id,
        effective_margin_pct=with_support,
        required_margin_pct=required,
        checks=(outcome,),
    )
    return item, result


def test_the_order_margin_counts_each_line_with_its_support() -> None:
    # The business case: 0,94 % in the file, but every line is above 20 % with its support.
    items = [
        supported("1000", "1", D("0.01"), D("0.22"), D("0.18")),
        supported("1000", "1", D("-0.05"), D("0.20"), D("0.18"), row=3),
        pair("2000", "1", D("0.25"), D("0.18"), "cumple_margen_lista", row=4),
    ]

    margin, required = rules.order_margins(items)

    assert margin == D("0.230000") and required == D("0.180000")
    assert [rules.line_margin(item, result) for item, result in items] == [
        D("0.22"),
        D("0.20"),
        D("0.25"),
    ]


def test_only_an_active_support_with_money_changes_the_line_margin() -> None:
    in_review = supported("100", "1", D("0.01"), D("0.22"), D("0.18"), needs_review=True)
    exception = supported("100", "1", D("0.01"), D("0.22"), D("0.18"), calc_method="margen_minimo")
    unmet = supported("100", "1", D("0.01"), D("0.15"), D("0.18"), met=False)

    assert rules.line_margin(*in_review) == D("0.01")
    assert rules.line_margin(*exception) == D("0.01")
    # A support that falls short still brings its money to the order.
    assert rules.line_margin(*unmet) == D("0.15")
    assert rules.points_below_target(*unmet) == D("0.030000")


def test_tolerance_releases_an_order_at_18_with_up_to_two_lines_within_4_points() -> None:
    # Example of the business: target 12 %, line at 8 % (4 points), the order at 18 % or more.
    good = pair("8000", "1", D("0.20"), D("0.20"), "cumple_margen_lista", row=3)
    one = [pair("1000", "1", D("0.08"), D("0.12")), good]
    two = [*one, pair("1000", "1", D("0.09"), D("0.20"), row=4)]

    assert rules.order_margins(one)[0] == D("0.186667")
    # The order target is above the margin, so only the tolerance releases it.
    assert rules.release_by_margin(one, D("0")) == "tolerancia"
    assert rules.release_basis(one, False, D("0")) == "tolerancia"
    assert rules.release_by_margin(two, D("0")) is None  # 0,09 vs 0,20 is 11 points short
    two_close = [*one, pair("1000", "1", D("0.17"), D("0.20"), row=4)]
    assert rules.release_by_margin(two_close, D("0")) == "tolerancia"
    three_close = [*two_close, pair("1000", "1", D("0.19"), D("0.20"), row=5)]
    assert rules.order_margins(three_close)[0] >= D("0.18")
    assert rules.release_by_margin(three_close, D("0")) is None  # three lines below target


def test_tolerance_needs_the_order_at_the_minimum_margin() -> None:
    items = [
        pair("1000", "1", D("0.08"), D("0.12")),
        pair("1000", "1", D("0.20"), D("0.20"), "cumple_margen_lista", row=3),
    ]

    assert rules.order_margins(items) == (D("0.140000"), D("0.160000"))
    assert rules.release_by_margin(items, D("0")) is None
    # The limits come from the policy in force.
    loose = rules.OrderPolicy(tolerance_min_order_margin=D("0.14"))
    assert rules.release_by_margin(items, D("0"), loose) == "tolerancia"


def test_a_line_more_than_4_points_below_its_target_holds_the_order_and_is_flagged() -> None:
    far = pair("100", "1", D("0.07"), D("0.12"))  # 5 points short
    big = pair("100000", "1", D("0.40"), D("0.12"), "cumple_margen_lista", row=3)
    items = [far, big]

    # The order margin is far above its target, yet nothing but a person releases it.
    assert rules.order_margins(items)[0] > D("0.39")
    assert rules.release_by_margin(items, D("0.05")) is None
    assert rules.release_basis(items, False, D("0.05")) is None
    assert rules.release_basis(items, True, D("0")) == "manual"
    assert rules.line_alerts(*far) == ("bajo_objetivo",)
    assert rules.line_alerts(*big) == ()
    (alert,) = rules.order_alerts(items)
    assert alert.code == "bajo_objetivo" and alert.row_numbers == (2,)
    assert "más de 4,00 puntos" in alert.message
    # Exactly 4 points is still within the tolerance.
    assert rules.line_alerts(*pair("100", "1", D("0.08"), D("0.12"))) == ()


def test_a_full_margin_product_is_only_released_by_hand() -> None:
    full = pair("1000", "1", D("1"), D("0.20"), "margen_100", row=3)
    items = [pair("1000", "1", D("0.30"), D("0.20"), "cumple_margen_lista"), full]

    assert rules.release_by_margin(items, D("0.05")) is None
    assert rules.release_basis(items, False, D("0.05")) is None
    assert rules.release_basis(items, True, D("0")) == "manual"
    assert rules.summarize_batch(items) == (2, 1, 1, 0, 0)
    assert rules.summarize_batch(items, {"PV-1"}) == (2, 1, 1, 0, 1)
    (alert,) = rules.order_alerts(items)
    assert alert.code == "margen_100" and alert.row_numbers == (3,)
    # It stays out of the order margin: it carries no cost.
    assert rules.order_margins(items)[0] == D("0.300000")
