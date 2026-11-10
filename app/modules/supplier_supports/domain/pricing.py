"""Amounts and margins of a support, derived from the stored inputs.

The workbook recomputed these with fragile per-sheet formulas (hard-coded 18%,
19%, 0.81, ...). Here every ``calc_method`` has one definition, taken from the
workbook's ``Formulas`` sheet:

- ``% margin = 1 - cost / price``
- ``price = cost / (1 - margin)``
- ``recomposed margin = 1 - (cost - supplier amount) / price``

Amounts are expressed in the unit of ``reference_cost`` (a box or a unit); the
functions never mix units.
"""

from __future__ import annotations

import math
from dataclasses import replace
from decimal import Decimal

from app.modules.supplier_supports.domain.entities import Derived, MoneyPair, SupportData
from app.modules.supplier_supports.domain.units import to_box_and_unit

ONE = Decimal(1)
ZERO = Decimal(0)


def _div(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _margin_price(cost: Decimal | None, margin: Decimal | None) -> Decimal | None:
    if cost is None or margin is None or margin >= ONE:
        return None
    return cost / (ONE - margin)


def supplier_amount(data: SupportData) -> Decimal | None:
    """Money the supplier funds per cost unit, or ``None`` when it is not per-unit.

    Args:
        data (SupportData): The support inputs.

    Returns:
        Decimal | None: Supplier amount; ``None`` for rebates over sales,
        margin-only exceptions, or when a required reference is missing.
    """

    value, cost, listed = data.supplier_value, data.reference_cost, data.reference_list_price
    method = data.calc_method
    if value is None:
        return None
    if method == "pct_costo":
        return cost * value if cost is not None else None
    if method == "pct_precio_lista":
        return listed * value if listed is not None else None
    if method == "valor_unidad":
        return value
    if method == "costo_neto":
        return cost - value if cost is not None else None
    if method == "precio_neto":
        if cost is None or data.target_margin_pct is None:
            return None
        return cost - value * (ONE - data.target_margin_pct)
    return None


def dwh_cost_in_unit(
    unit_cost: Decimal | None,
    value_unit: str | None,
    pack_factor: Decimal | None,
    reference_cost: Decimal | None = None,
) -> Decimal | None:
    """The DWH cost (per inventory unit) in the unit the support's values are written in.

    A support "per box" is the DWH cost times the pack factor, except when the SKU itself is
    the box (Ohra: the SKU is a case of 18 bottles and its inventory unit is the case, while
    the sheet's U.E says 18). When the support has a reference cost, the reading closest to
    it wins, the same check the unit inference uses (``inferido_dwh``).

    ``None`` when it cannot be placed (the support's unit is unknown, or a box with no pack
    factor and no reference): then the support keeps its reference cost.
    """

    if unit_cost is None:
        return None
    if value_unit in ("unidad", "kg"):
        return unit_cost
    if value_unit != "caja":
        return None
    boxed = unit_cost * pack_factor if pack_factor else None
    if reference_cost is None or reference_cost <= 0:
        return boxed
    candidates = [value for value in (boxed, unit_cost) if value is not None and value > 0]
    return min(candidates, key=lambda value: abs(math.log(value / reference_cost)))


def derive(data: SupportData, dwh_unit_cost: Decimal | None = None) -> Derived:
    """Compute the derived amounts and margins of a support.

    The cost is the DWH's (the quoter's rule, refreshed daily) whenever it is known and can
    be put in the support's unit; otherwise the reference cost stored with the support.
    ``cost_basis`` says which one was used.

    Args:
        data (SupportData): The support inputs.
        dwh_unit_cost (Decimal | None): The DWH cost per inventory unit of its SKU.

    Returns:
        Derived: Whatever can be computed with the references available.
    """

    basis = "referencia" if data.reference_cost is not None else None
    live = dwh_cost_in_unit(dwh_unit_cost, data.value_unit, data.pack_factor, data.reference_cost)
    if live is not None:
        data = replace(data, reference_cost=live)
        basis = "dwh"
    cost, listed = data.reference_cost, data.reference_list_price
    amount = supplier_amount(data)
    net_cost = cost - amount if cost is not None and amount is not None else None
    jdh_amount = listed * data.jdh_pct if listed is not None and data.jdh_pct is not None else None
    if data.calc_method == "costo_neto" and data.jdh_net_cost is not None:
        # JDH changes the effective pact, never the supplier-funded amount.
        jdh_amount = net_cost - data.jdh_net_cost if net_cost is not None else None
        net_cost = data.jdh_net_cost

    min_margin = data.min_margin_pct
    if data.calc_method == "puntos_margen" and data.supplier_value is not None:
        base = data.list_margin_pct
        min_margin = base - data.supplier_value if base is not None else min_margin

    price: Decimal | None = None
    if data.calc_method == "precio_neto":
        price = data.supplier_value
    elif data.calc_method == "pct_precio_lista" and listed is not None:
        price = listed * (ONE - (data.supplier_value or ZERO) - (data.jdh_pct or ZERO))
    elif net_cost is not None:
        price = _margin_price(net_cost, data.target_margin_pct or data.list_margin_pct)

    margin_order = None if price is None else _complement(_div(cost, price))
    margin_recomposed = None if price is None else _complement(_div(net_cost, price))

    def per(value: Decimal | None) -> MoneyPair:
        return MoneyPair(*to_box_and_unit(value, data.value_unit, data.pack_factor))

    return Derived(
        supplier_amount=amount,
        jdh_amount=jdh_amount,
        net_cost=net_cost,
        pct_on_cost=_div(amount, cost),
        pct_on_list=_div(amount, listed),
        suggested_price=price,
        margin_order=margin_order,
        margin_recomposed=margin_recomposed,
        min_margin_pct=min_margin,
        cost=per(cost),
        list_price=per(listed),
        supplier_amount_per=per(amount),
        net_cost_per=per(net_cost),
        suggested_price_per=per(price),
        cost_basis=basis,
    )


def _complement(value: Decimal | None) -> Decimal | None:
    return None if value is None else ONE - value
