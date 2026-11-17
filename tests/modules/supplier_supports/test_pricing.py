"""Derived amounts, prices and margins (rules v2), per inventory unit."""

from decimal import Decimal

import pytest

from app.modules.supplier_supports.domain.entities import SupportData
from app.modules.supplier_supports.domain.pricing import (
    derive,
    minimum_price,
    supplier_amount,
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


def test_guaranteed_margin_starts_from_an_agreed_supported_price() -> None:
    # PA 5.000 agreed with 18 % guaranteed: $Apoyo = Costo - PA x (1 - 18 %) = 820.
    result = derive(
        data(calc_method="precio_neto", supplier_value=D("5000"), target_margin_pct=D("0.18")),
        dwh_unit_cost=D("4920"),
        product_margin=D("0.18"),
    )

    assert result.supplier_amount == D("820.00")
    assert result.suggested_price == D("5000")
    assert round(result.margin_recomposed, 6) == D("0.18")


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
    assert result.customer_discount_pct == 1 - result.supported_price / D("6000")


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
