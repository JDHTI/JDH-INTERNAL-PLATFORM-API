"""Unit of a cost (per unit or per box) and the unit/box conversions."""

from decimal import Decimal

import pytest

from app.modules.supplier_supports.domain.entities import SupportData
from app.modules.supplier_supports.domain.pricing import derive
from app.modules.supplier_supports.domain.units import infer_value_unit, to_box_and_unit

D = Decimal


@pytest.mark.parametrize(
    ("cost", "unit_cost", "pack", "expected"),
    [
        # Real rows: Maizena x720g (24 per box) costs 13.783 in the sheet and 13.782,70 in the DWH
        (D("13783"), D("13782.70"), D("24"), "unidad"),
        # Fruco Gx4350g (4 per box): 59.770 in the sheet vs 60.245,58 per unit in the DWH
        (D("59770.25"), D("60245.58"), D("4"), "unidad"),
        # A box of 24 priced at roughly 24 units
        (D("330000"), D("13782.70"), D("24"), "caja"),
        # Negotiated cost 15 % under the moving average is still a unit cost
        (D("11700"), D("13782.70"), D("24"), "unidad"),
        # Neither one unit nor one box: left undecided instead of guessed
        (D("60000"), D("13782.70"), D("24"), None),
        # Nothing to compare with, or a one-unit box (both readings are the same)
        (None, D("100"), D("6"), None),
        (D("100"), None, D("6"), None),
        (D("100"), D("100"), D("1"), None),
        (D("100"), D("100"), None, None),
        (D("0"), D("100"), D("6"), None),
    ],
)
def test_infer_value_unit(cost, unit_cost, pack, expected) -> None:
    assert infer_value_unit(cost, unit_cost, pack) == expected


@pytest.mark.parametrize(
    ("value", "unit", "pack", "expected"),
    [
        (D("120"), "caja", D("6"), (D("120"), D("20"))),
        (D("20"), "unidad", D("6"), (D("120"), D("20"))),
        (D("120"), "caja", None, (D("120"), None)),  # no pack factor: no unit price
        (D("20"), "unidad", None, (None, D("20"))),  # no pack factor: no box price
        (D("5"), "kg", D("6"), (None, None)),
        (D("5"), None, D("6"), (None, None)),
        (None, "caja", D("6"), (None, None)),
    ],
)
def test_to_box_and_unit(value, unit, pack, expected) -> None:
    assert to_box_and_unit(value, unit, pack) == expected


def test_derived_values_come_per_box_and_per_unit() -> None:
    # Familia: a cost per box of 8, supported down to 80 per box.
    result = derive(
        SupportData(
            kind="tactico_cliente",
            calc_method="costo_neto",
            supplier_value=D("80"),
            reference_cost=D("100"),
            reference_list_price=D("125"),
            target_margin_pct=D("0.2"),
            value_unit="caja",
            pack_factor=D("8"),
        )
    )

    assert (result.cost.box, result.cost.unit) == (D("100"), D("12.5"))
    assert (result.list_price.box, result.list_price.unit) == (D("125"), D("15.625"))
    assert (result.net_cost_per.box, result.net_cost_per.unit) == (D("80"), D("10"))
    assert (result.supplier_amount_per.box, result.supplier_amount_per.unit) == (D("20"), D("2.5"))
    # PA = PL - $Apoyo = 15,625 - 2,5 per unit.
    assert (result.suggested_price_per.box, result.suggested_price_per.unit) == (
        D("105"),
        D("13.125"),
    )


def test_a_cost_per_unit_gives_the_box_price_from_the_pack_factor() -> None:
    # Diversey: the cost is per unit; the box follows from the pack factor when it is known.
    result = derive(
        SupportData(
            kind="tactico_cliente",
            calc_method="pct_costo",
            supplier_value=D("0.12"),
            reference_cost=D("11201"),
            value_unit="unidad",
            pack_factor=D("4"),
        )
    )

    assert result.supplier_amount_per.unit == D("1344.12")
    assert result.supplier_amount_per.box == D("5376.48")
    assert result.net_cost_per.unit == D("9856.88")


def test_without_a_known_unit_the_value_is_read_per_unit() -> None:
    result = derive(
        SupportData(
            kind="tactico_cliente",
            calc_method="pct_costo",
            supplier_value=D("0.05"),
            reference_cost=D("100"),
            pack_factor=D("6"),
        )
    )

    assert result.net_cost == D("95.00")
    assert (result.net_cost_per.box, result.net_cost_per.unit) == (D("570.00"), D("95.00"))
