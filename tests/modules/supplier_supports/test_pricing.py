"""Derived amounts, prices and margins (rules v2), per inventory unit."""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.modules.supplier_supports.domain.entities import SupportData
from app.modules.supplier_supports.domain.pricing import (
    derive,
    fixed_guaranteed_amount,
    guaranteed_amount,
    guaranteed_share,
    minimum_price,
    supplier_amount,
    supplier_supported_price,
    unit_amount,
)

D = Decimal


def data(**values) -> SupportData:
    base = {"kind": "tactico_cliente", "calc_method": "pct_costo", "value_unit": "unidad"}
    return SupportData(**{**base, **values})


def test_bi_control_case_10_percent_on_the_list_price() -> None:
    # Cost 4.920 and list 6.000 (margin 18 %): 10 % of the list is 600 off; PA 5.400; 20 %.
    result = derive(
        data(calc_method="pct_precio_lista", supplier_value=D("0.10")),
        dwh_unit_cost=D("4920"),
        product_margin=D("0.18"),
    )

    assert result.list_price.unit == D("6000")
    assert result.supplier_amount == D("600")
    assert result.suggested_price == D("5400")
    assert result.margin_recomposed == D("0.2")
    # The money is the absolute amount of the list price, not the same % of the cost.
    assert result.net_cost == D("4320")


@pytest.mark.parametrize(
    ("method", "value", "amount"),
    [
        ("pct_costo", D("0.05"), D("246")),  # Costo x %
        ("pct_precio_lista", D("0.05"), D("300")),  # PL x %
        ("valor_unidad", D("250"), D("250")),  # $ in cost
        ("valor_precio_lista", D("250"), D("250")),  # $ off the list price
        ("costo_neto", D("4500"), D("420")),  # Costo - $CostoApoyado
    ],
)
def test_each_type_gives_the_supplier_money_and_the_suggested_price(method, value, amount):
    result = derive(
        data(calc_method=method, supplier_value=value),
        dwh_unit_cost=D("4920"),
        product_margin=D("0.18"),
    )

    assert result.supplier_amount == amount
    # PA = PL - $Apoyo; recomposed margin = (PA - Costo + $Apoyo) / PA.
    assert result.suggested_price == D("6000") - amount
    price = result.suggested_price
    assert result.margin_recomposed == (price - D("4920") + amount) / price


def test_a_guaranteed_margin_is_shown_at_the_list_price() -> None:
    # Cost 4.920 at 18 % -> PL 6.000. The supplier guarantees 25 % at whatever price: at PL
    # it pays 4.920 - 6.000 x 75 % = 420 and the margin is 25 %. It has no value of its own.
    result = derive(
        data(calc_method="precio_neto", supplier_value=D("5000"), target_margin_pct=D("0.25")),
        dwh_unit_cost=D("4920"),
        product_margin=D("0.18"),
    )

    assert result.supplier_amount == D("420.00")
    assert result.suggested_price == D("6000")
    assert round(result.margin_recomposed, 6) == D("0.25")
    assert result.supported_price is None  # no minimum price: it covers any price
    # A guarantee under the list margin pays nothing there: the margin stays at 18 %.
    low = derive(
        data(calc_method="precio_neto", target_margin_pct=D("0.10")),
        dwh_unit_cost=D("4920"),
        product_margin=D("0.18"),
    )
    assert low.supplier_amount == D("0") and round(low.margin_recomposed, 6) == D("0.18")
    assert not low.support_above_cost


@pytest.mark.parametrize(
    ("supplier_min", "price", "amount"),
    [
        # Cost 1.000, 18 % guaranteed. At 13 %: PA 1.149,43, $ Apoyo 57,47.
        (D("0.13"), D("1149.425287"), D("57.471264")),
        # An exception at 0 %: PA is the cost, it pays 18 % of it.
        (D("0"), D("1000"), D("180")),
        # A negative exception (-5 %): PA under the cost, it pays more.
        (D("-0.05"), D("952.380952"), D("219.047619")),
    ],
)
def test_the_supplier_minimum_sets_the_supported_price_and_a_fixed_amount(
    supplier_min, price, amount
) -> None:
    """2026-11-23: PA = cost / (1 − supplier minimum); $ Apoyo = cost − PA × (1 − g)."""

    assert round(supplier_supported_price(D("1000"), supplier_min), 6) == price
    assert round(fixed_guaranteed_amount(D("1000"), D("0.18"), supplier_min), 6) == amount
    # The same amount whatever the price it is sold at.
    assert unit_amount(
        "precio_neto",
        None,
        cost=D("1000"),
        list_price=D("1333"),
        guaranteed_margin=D("0.18"),
        sale_price=D("2000"),
        supplier_min_margin=supplier_min,
    ) == fixed_guaranteed_amount(D("1000"), D("0.18"), supplier_min)


def test_a_guaranteed_margin_with_the_supplier_minimum_derives_its_prices() -> None:
    # Cost 1.000 at the product's 25 % -> PL 1.333,33; 18 % guaranteed from a 13 % minimum.
    result = derive(
        data(
            calc_method="precio_neto",
            target_margin_pct=D("0.18"),
            supplier_min_margin_pct=D("0.13"),
        ),
        dwh_unit_cost=D("1000"),
        product_margin=D("0.25"),
    )

    assert round(result.supplier_amount, 2) == D("57.47")
    # Suggested = PL − $ Apoyo, like the other types; the minimum is the supported price.
    assert round(result.suggested_price, 2) == D("1275.86")
    assert round(result.supported_price, 2) == D("1149.43")
    # At the supported price the margin with the support is the guaranteed one.
    assert round(result.margin_recomposed, 6) == D("0.18")
    # The JDH authorized margin stays its own: it is not the guarantee.
    assert result.supported_margin_pct is None


def test_a_guaranteed_margin_pays_what_is_missing_at_the_price_sold() -> None:
    # The user's example: sold at 10.000 with 10 % margin (cost 9.000); 14 % guaranteed.
    assert guaranteed_amount(D("9000"), D("10000"), D("0.14")) == D("400.00")
    assert guaranteed_share(D("0.10"), D("0.14")) * D("9000") == pytest.approx(D("400"))
    # At 12.000 the margin is already 25 %: nothing to pay.
    assert guaranteed_amount(D("9000"), D("12000"), D("0.14")) == D("0")


@pytest.mark.parametrize(
    ("method", "value", "net_cost"),
    [
        ("pct_costo", D("0.05"), D("4674")),  # (Costo - Costo x %) / (1 - m)
        ("pct_precio_lista", D("0.05"), D("4620")),  # (Costo - PL x %) / (1 - m)
        ("valor_unidad", D("250"), D("4670")),  # (Costo - $) / (1 - m)
        ("valor_precio_lista", D("250"), D("4670")),
        ("costo_neto", D("4500"), D("4500")),  # $CostoApoyado / (1 - m)
    ],
)
def test_jdh_minimum_margin_gives_the_minimum_supported_price(method, value, net_cost) -> None:
    result = derive(
        data(calc_method=method, supplier_value=value, supported_margin_pct=D("0.08")),
        dwh_unit_cost=D("4920"),
        product_margin=D("0.18"),
    )

    assert result.supported_margin_pct == D("0.08")
    assert result.supported_price == net_cost / D("0.92")
    assert result.customer_discount_pct is None  # disabled for now


def test_movistar_arena_22505_supported_cost_per_box_and_jdh_margin() -> None:
    # Familia supports 110.393 per box of 12 (9.199,42 per unit); the DWH cost is 11.233,38
    # per unit; JDH authorizes going down to a 10 % margin on top.
    result = derive(
        data(
            calc_method="costo_neto",
            value_unit="caja",
            supplier_value=D("110393"),
            supported_margin_pct=D("0.10"),
        ),
        dwh_unit_cost=D("11233.38"),
        product_margin=D("0.25"),
        product_pack_factor=D("12"),
    )

    unit_cost = D("110393") / 12
    listed = D("11233.38") / D("0.75")
    assert result.net_cost == unit_cost
    assert result.supplier_amount == D("11233.38") - unit_cost
    assert result.suggested_price == listed - (D("11233.38") - unit_cost)
    assert result.supported_price == unit_cost / D("0.9")
    assert result.supported_price_per.box == unit_cost / D("0.9") * 12


def test_22506_recomposed_margin_is_at_the_suggested_or_the_minimum_price() -> None:
    # 22506: the SKU is the box (no pack factor); supported cost 118.400, DWH cost 133.736,
    # Food service 18 %: list price 163.092,68; suggested price = list - 15.336.
    support = data(calc_method="costo_neto", value_unit="caja", supplier_value=D("118400"))

    result = derive(support, D("133736"), D("0.18"), None)

    listed = D("133736") / D("0.82")
    assert result.suggested_price == listed - (D("133736") - D("118400"))
    # At the suggested price (19,9 %), never at the list price (27,4 %).
    assert result.margin_recomposed == 1 - D("118400") / result.suggested_price
    assert round(result.margin_recomposed, 3) == D("0.199")
    assert result.recomposed_list_margin == result.margin_recomposed
    # With JDH's 10 % minimum, at the minimum price: 10 %.
    jdh = derive(replace(support, supported_margin_pct=D("0.10")), D("133736"), D("0.18"), None)
    assert jdh.supported_price == D("118400") / D("0.9")
    assert round(jdh.margin_recomposed, 6) == D("0.1")


def test_a_supported_cost_above_todays_cost_counts_as_no_support() -> None:
    # 23677: 64.482 per box of 24 (2.686,75 per unit) while the DWH cost is 2.158 per unit.
    result = derive(
        data(calc_method="costo_neto", value_unit="caja", supplier_value=D("64482")),
        dwh_unit_cost=D("2158"),
        product_margin=D("0.20"),
        product_pack_factor=D("24"),
    )

    assert result.support_above_cost is True
    assert result.supplier_amount == 0 and result.net_cost == D("2158")
    # The suggested price never goes above the list price.
    assert result.suggested_price == result.list_price.unit == D("2697.5")
    below = derive(
        data(calc_method="costo_neto", value_unit="caja", supplier_value=D("48000")),
        D("2158"),
        D("0.20"),
        D("24"),
    )
    assert below.support_above_cost is False and below.supplier_amount == D("158")


def test_the_three_skus_bi_checked_are_priced_per_unit() -> None:
    # 26593 (box of 12), 16594 and 24956 (boxes of 24): supported cost per box, DWH per unit.
    for value, pack, cost, expected in (
        (D("119927"), D("12"), D("13893"), D("13893") - D("119927") / 12),
        (D("103862"), D("24"), D("6407"), D("6407") - D("103862") / 24),
        (D("87921"), D("24"), D("5517"), D("5517") - D("87921") / 24),
    ):
        result = derive(
            data(calc_method="costo_neto", value_unit="caja", supplier_value=value),
            cost,
            D("0.18"),
            pack,
        )
        assert result.supplier_amount == expected and result.cost.unit == cost
        assert result.suggested_price_per.box == result.suggested_price * pack


def test_a_box_value_is_divided_by_the_master_pack_factor_and_null_is_one() -> None:
    boxed = data(calc_method="valor_unidad", value_unit="caja", supplier_value=D("1200"))

    assert derive(boxed, D("1000"), D("0.2"), D("12")).supplier_amount == D("100")
    # No pack factor: the SKU itself is the box.
    assert derive(boxed, D("1000"), D("0.2"), None).supplier_amount == D("1200")
    # The master's factor wins over the one stored with the support (the sheet's U.E).
    stored = data(
        calc_method="valor_unidad", value_unit="caja", supplier_value=D("1800"), pack_factor=18
    )
    assert derive(stored, D("1000"), D("0.2"), D("1")).supplier_amount == D("1800")


def test_without_the_dwh_the_reference_cost_is_brought_to_units() -> None:
    result = derive(
        data(value_unit="caja", supplier_value=D("0.05"), reference_cost=D("120000")),
        product_margin=D("0.2"),
        product_pack_factor=D("12"),
    )

    assert result.cost_basis == "referencia" and result.cost.unit == D("10000")
    assert result.cost.box == D("120000") and result.supplier_amount == D("500")


def test_margin_exception_has_no_money_but_a_minimum_price() -> None:
    exception = data(kind="excepcion_margen", calc_method="margen_minimo", min_margin_pct=D("0.1"))

    result = derive(exception, D("900"), D("0.2"))

    assert result.supplier_amount is None and result.suggested_price is None
    assert result.supported_price == D("1000")


def test_puntos_margen_keeps_subtracting_from_the_list_margin() -> None:
    result = derive(
        data(calc_method="puntos_margen", supplier_value=D("0.10"), list_margin_pct=D("0.25"))
    )

    assert result.min_margin_pct == D("0.15")
    assert result.supplier_amount is None


def test_rebates_and_margin_only_rows_have_no_per_unit_amount() -> None:
    assert supplier_amount(data(calc_method="pct_ventas", supplier_value=D("0.05"))) is None
    assert supplier_amount(data(calc_method="margen_minimo", min_margin_pct=D("0.1"))) is None


def test_missing_references_yield_none_instead_of_failing() -> None:
    result = derive(data(supplier_value=D("0.05")))

    assert result.supplier_amount is None
    assert result.suggested_price is None
    assert result.margin_order is None


def test_shared_amount_and_minimum_price_functions() -> None:
    assert unit_amount(
        "valor_precio_lista",
        D("2400"),
        cost=D("1000"),
        list_price=None,
        value_unit="caja",
        pack_factor=D("24"),
    ) == D("100")
    assert minimum_price(D("900"), D("1")) is None
    assert minimum_price(D("900"), D("0.1")) == D("1000")
