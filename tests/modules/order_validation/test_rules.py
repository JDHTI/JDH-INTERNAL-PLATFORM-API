"""Release decision of a retained line against the supports master."""

from datetime import date
from decimal import Decimal

from app.modules.order_validation.domain import rules
from app.modules.order_validation.domain.entities import CustomerScope, ProductInfo
from tests.modules.order_validation.conftest import agreement, line

TODAY = date(2026, 10, 9)
D = Decimal


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

    assert result.status == "pendiente_validacion" and result.reason == "datos_incompletos"
    assert "no trae costo" in result.detail


def test_order_status_and_batch_counts() -> None:
    ready, pending = "listo_para_liberar", "pendiente_validacion"

    assert rules.order_status([ready, ready]) == ready
    assert rules.order_status([ready, pending]) == pending
    items = [
        (line(document=document), rules.LineResult(status, "sin_acuerdo", ""))
        for document, status in (("A", ready), ("A", pending), ("B", ready))
    ]
    assert rules.summarize_batch(items) == (3, 2, 2, 1)
    # A manual approval makes the order ready; its lines keep their status.
    assert rules.summarize_batch(items, {"A"}) == (3, 2, 2, 2)


def test_the_margin_of_the_channel_is_the_reference() -> None:
    product = info(list=D("0.18"), wholesale=D("0.12"), salsamentarias=D("0.10"))

    assert decide(product=product, channel="HOTELES Y CLUBES").required_margin_pct == D("0.18")
    wholesale = decide(product=product, channel="MAYORISTAS Y DISTRIBUIDORES")
    assert wholesale.required_margin_pct == D("0.12") and wholesale.margin_basis == "mayoristas"
    assert decide(product=product, channel="SUBDISTRIBUIDOR").margin_basis == "mayoristas"
    sal = decide(product=product, channel="SALSAMENTARIAS", margin_pct=D("0.10"))
    assert sal.reason == "cumple_margen_lista" and sal.margin_basis == "salsamentarias"
    assert sal.detail == "Margen del pedido 10,00 % ≥ margen salsamentarias 10,00 %"
    # Masivo Food and Market Apartadó are loaded but no rule applies them yet.
    other = ProductInfo("1", {"margin_list": D("0.30"), "margin_mass_food": D("0.01")})
    assert decide(product=other, channel="AUTOSERVICIO").required_margin_pct == D("0.30")


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
    # 1 000 at 10 % and 3 000 at 20 %: the order margin is 17,5 % against a 20 % target.
    items = [
        pair("1000", "1", D("0.10"), D("0.20")),
        pair("3000", "1", D("0.20"), D("0.20"), "cumple_margen_lista"),
    ]

    assert rules.order_margins(items) == (D("0.175000"), D("0.200000"))
    assert not rules.release_by_threshold(items, D("0"))
    assert not rules.release_by_threshold(items, D("0.02"))
    assert rules.release_by_threshold(items, D("0.025"))
    assert rules.release_by_threshold(items, D("0.03"))
    assert rules.release_basis(items, False, D("0.03")) == "margen"
    assert rules.release_basis(items, False, D("0")) is None
    assert rules.release_basis(items, True, D("0")) == "manual"


def test_the_threshold_example_of_the_business_19_5_against_20() -> None:
    items = [pair("100", "10", D("0.195"), D("0.20"), "no_cumple_acuerdo")]

    assert not rules.release_by_threshold(items, D("0"))
    assert rules.release_by_threshold(items, D("0.005"))
    assert not rules.release_by_threshold([pair("100", "10", D("0.194"), D("0.20"))], D("0.005"))


def test_the_threshold_does_not_rescue_orders_that_need_a_person() -> None:
    short = pair("1000", "1", D("0.199"), D("0.20"))
    for reason in (
        "acuerdo_en_revision",
        "acuerdo_no_evaluable",
        "acuerdo_vencido",
        "datos_incompletos",
    ):
        other = pair("1000", "1", D("0.30"), D("0.20"), reason, row=3)
        assert not rules.release_by_threshold([short, other], D("0.05"))
    # A line with nothing to compare with, or with no cost, keeps the order pending too.
    assert not rules.release_by_threshold(
        [short, pair("1000", "1", D("0.30"), None, row=3)], D("0.05")
    )
    assert not rules.release_by_threshold(
        [short, pair("1000", "1", D("1"), D("0.20"), row=3)], D("0.05")
    )
    assert rules.release_by_threshold(
        [short, pair("1000", "1", D("0.30"), D("0.20"), "cumple_margen_lista", row=3)], D("0.05")
    )


def test_a_line_is_never_released_by_the_threshold() -> None:
    result = decide(reference=D("0.20"), margin_pct=D("0.195"))

    assert result.status == "pendiente_validacion" and result.reason == "sin_acuerdo"
    support = agreement(supplier_value=D("0.05"), target_margin_pct=D("0.1455"))
    assert decide([support]).reason == "no_cumple_acuerdo"


def test_a_product_with_liquor_tax_is_flagged_whatever_the_decision() -> None:
    taxed = ProductInfo("18294", {"margin_list": D("0.20")}, liquor_tax=D("6911"))

    pending = decide(product=taxed, margin_pct=D("0.05"))
    ready = decide(product=taxed, margin_pct=D("0.25"))

    assert pending.liquor_tax and pending.liquor_tax_amount == D("6911")
    assert ready.liquor_tax and ready.status == "listo_para_liberar"
    assert not decide(product=info(list=D("0.2"))).liquor_tax
    assert not decide(product=ProductInfo("1", liquor_tax=D("0"))).liquor_tax
    assert decide(product=taxed, margin_pct=None).liquor_tax


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
