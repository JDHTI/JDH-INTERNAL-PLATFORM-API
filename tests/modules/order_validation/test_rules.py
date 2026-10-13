"""Release decision of a retained line against the supports master."""

from datetime import date
from decimal import Decimal

from app.modules.order_validation.domain import rules
from app.modules.order_validation.domain.entities import CustomerScope

from tests.modules.order_validation.conftest import agreement, line

TODAY = date(2026, 10, 9)
D = Decimal


def decide(agreements=(), customer=None, supplier_margin=None, **values):
    return rules.decide(line(**values), list(agreements), customer, supplier_margin, TODAY)


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

    assert decide([support], supplier_margin=D("0.14")).reason == "cumple_con_apoyo"
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
    result = decide(supplier_margin=D("0.10"))

    assert result.status == "listo_para_liberar" and result.reason == "cumple_margen_lista"


def test_no_agreement_and_low_margin_is_pending() -> None:
    other_client = agreement(customer_nit="800116299", supplier_value=D("0.5"))

    result = decide([other_client], supplier_margin=D("0.18"))

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
    result = decide(margin_pct=D("1"), supplier_margin=D("0.18"))

    assert result.status == "pendiente_validacion" and result.reason == "datos_incompletos"
    assert "no trae costo" in result.detail


def test_order_status_and_batch_counts() -> None:
    ready, pending = "listo_para_liberar", "pendiente_validacion"

    assert rules.order_status([ready, ready]) == ready
    assert rules.order_status([ready, pending]) == pending
    assert rules.summarize([("A", ready), ("A", pending), ("B", ready)]) == (3, 2, 2, 1)
