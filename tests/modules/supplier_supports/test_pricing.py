"""Derived amounts and margins, checked against rows of the supports workbook."""

from decimal import Decimal

from app.modules.supplier_supports.domain.entities import SupportData
from app.modules.supplier_supports.domain.pricing import derive, supplier_amount

D = Decimal


def data(**values) -> SupportData:
    base = {"kind": "tactico_cliente", "calc_method": "pct_costo"}
    return SupportData(**{**base, **values})


def test_pct_costo_team_panaderia_4_8_percent_on_cost() -> None:
    # TEAM-PANADERIA: support = cost per case x 4.8%; net cost = cost - support.
    result = derive(data(supplier_value=D("0.048"), reference_cost=D("100000")))

    assert result.supplier_amount == D("4800.000")
    assert result.net_cost == D("95200.000")
    assert result.pct_on_cost == D("0.048")


def test_costo_neto_familia_derives_the_support_percent() -> None:
    # Familia: COSTO APOYADO CAJA is the input; % APOYO DCTO COSTO = 1 - apoyado / costo.
    result = derive(
        data(
            calc_method="costo_neto",
            supplier_value=D("80"),
            reference_cost=D("100"),
            target_margin_pct=D("0.17"),
        )
    )

    assert result.supplier_amount == D("20")
    assert result.pct_on_cost == D("0.2")
    # PRECIO CAJA APOYADO = costo apoyado / (1 - margen objetivo)
    assert round(result.suggested_price, 4) == D("96.3855")
    # MARGEN REAL = 1 - costo apoyado / precio
    assert round(result.margin_recomposed, 4) == D("0.1700")
    # Margin of the order against the full cost is lower.
    assert round(result.margin_order, 4) == D("-0.0375")


def test_pct_precio_lista_unilever_credits_a_share_of_list_price_against_cost() -> None:
    # Unilever: supplier money = PRECIO LISTA x % DCTO APOYADO.
    result = derive(
        data(
            calc_method="pct_precio_lista",
            supplier_value=D("0.045"),
            reference_cost=D("81"),
            reference_list_price=D("100"),
        )
    )

    assert result.supplier_amount == D("4.500")
    assert result.net_cost == D("76.500")
    assert result.pct_on_list == D("0.045")
    assert result.suggested_price == D("95.5000")


def test_precio_neto_diversey_guaranteed_margin() -> None:
    # Diversey: support = cost - net price x (1 - 18%), so JDH keeps an 18% margin.
    result = derive(
        data(
            calc_method="precio_neto",
            supplier_value=D("90"),
            reference_cost=D("100"),
            target_margin_pct=D("0.18"),
        )
    )

    assert result.supplier_amount == D("26.20")
    assert result.net_cost == D("73.80")
    assert round(result.margin_recomposed, 6) == D("0.18")


def test_valor_unidad_fixed_amount_per_unit() -> None:
    result = derive(
        data(calc_method="valor_unidad", supplier_value=D("706"), reference_cost=D("5000"))
    )

    assert result.supplier_amount == D("706")
    assert result.pct_on_cost == D("0.1412")


def test_puntos_margen_subtracts_from_the_list_margin() -> None:
    # Rubbermaid: MARGEN MINIMO = MARGEN LISTA - % APOYO.
    result = derive(
        data(calc_method="puntos_margen", supplier_value=D("0.10"), list_margin_pct=D("0.25"))
    )

    assert result.min_margin_pct == D("0.15")
    assert result.supplier_amount is None


def test_jdh_discount_is_a_share_of_list_price() -> None:
    result = derive(
        data(
            calc_method="pct_costo",
            supplier_value=D("0.05"),
            jdh_pct=D("0.023"),
            reference_cost=D("100"),
            reference_list_price=D("120"),
        )
    )

    assert result.jdh_amount == D("2.760")


def test_rebates_and_margin_only_rows_have_no_per_unit_amount() -> None:
    assert supplier_amount(data(calc_method="pct_ventas", supplier_value=D("0.05"))) is None
    assert supplier_amount(data(calc_method="margen_minimo", min_margin_pct=D("0.1"))) is None


def test_missing_references_yield_none_instead_of_failing() -> None:
    result = derive(data(supplier_value=D("0.05")))

    assert result.supplier_amount is None
    assert result.suggested_price is None
    assert result.margin_order is None


def test_margin_of_one_or_more_never_divides_by_zero() -> None:
    result = derive(
        data(
            calc_method="costo_neto",
            supplier_value=D("80"),
            reference_cost=D("100"),
            target_margin_pct=D("1"),
        )
    )

    assert result.suggested_price is None
