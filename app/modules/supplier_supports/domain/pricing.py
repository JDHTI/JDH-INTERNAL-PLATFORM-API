"""Amounts, prices and margins of a support, derived from the stored inputs.

One definition per support type (``calc_method``), written by BI (rules v2, 2026-11-17).
Everything is per **inventory unit**: a value written per box is divided by the pack factor
of the product master (a missing factor counts as 1, the SKU itself is the box).

- **List price (PL)** = cost / (1 − optimal margin of the customer's margin list).
- **$ Apoyo**, the supplier's money per unit:

  ============================  ==================  ==========================================
  Type                          ``calc_method``     $ Apoyo
  ============================  ==================  ==========================================
  % Apoyo en Costo              pct_costo           Costo × %
  % Apoyo en Precio Lista       pct_precio_lista    PL × %
  $ Apoyo en Costo              valor_unidad        $
  $ Apoyo en Precio Lista       valor_precio_lista  $
  $ Costo Apoyado               costo_neto          Costo − $CostoApoyado
  % Margen Garantizado          precio_neto         Costo − PA × (1 − %MG), PA given
  ============================  ==================  ==========================================

- **Suggested supported price (PA)** = PL − $Apoyo (the price given for % Margen Garantizado).
- **Recomposed margin** = (PA − Costo + $Apoyo) / PA.
- **Minimum supported price** (JDH's authorized minimum margin ``m``, an exception) =
  (Costo − $Apoyo) / (1 − m); for $ Costo Apoyado that is $CostoApoyado / (1 − m).

Example (BI): cost 4.920, PL 6.000, 10 % on the list price → $Apoyo 600, PA 5.400,
recomposed margin (5.400 − 4.920 + 600) / 5.400 = 20 %.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from app.modules.supplier_supports.domain.entities import Derived, MoneyPair, SupportData

ONE = Decimal(1)
ZERO = Decimal(0)

# Types whose value is money (per unit or per box), not a fraction.
MONEY_METHODS = ("valor_unidad", "valor_precio_lista", "costo_neto", "precio_neto")
# Types that bring supplier money per unit.
AMOUNT_METHODS = ("pct_costo", "pct_precio_lista", *MONEY_METHODS)


def _div(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _complement(value: Decimal | None) -> Decimal | None:
    return None if value is None else ONE - value


def price_at_margin(cost: Decimal | None, margin: Decimal | None) -> Decimal | None:
    """``cost / (1 − margin)``; ``None`` without a cost or with a margin of 100 % or more."""

    if cost is None or margin is None or margin >= ONE:
        return None
    return cost / (ONE - margin)


def pack_of(pack_factor: Decimal | None) -> Decimal:
    """Units per box of the product master; a missing or non-positive factor is 1."""

    return pack_factor if pack_factor is not None and pack_factor > 0 else ONE


def per_unit(
    value: Decimal | None, value_unit: str | None, pack_factor: Decimal | None
) -> Decimal | None:
    """A money value of the support in inventory units (a box value ÷ the pack factor)."""

    if value is None:
        return None
    return value / pack_of(pack_factor) if value_unit == "caja" else value


def unit_amount(
    calc_method: str,
    value: Decimal | None,
    *,
    cost: Decimal | None,
    list_price: Decimal | None,
    value_unit: str | None = None,
    pack_factor: Decimal | None = None,
    guaranteed_margin: Decimal | None = None,
) -> Decimal | None:
    """The supplier's money per inventory unit ($ Apoyo), or ``None`` when it has none.

    Args:
        calc_method (str): The support type.
        value (Decimal | None): The support's value as written (a fraction or money).
        cost (Decimal | None): Cost per inventory unit.
        list_price (Decimal | None): List price per inventory unit.
        value_unit (str | None): ``caja`` when a money value is per box.
        pack_factor (Decimal | None): Units per box (product master; missing = 1).
        guaranteed_margin (Decimal | None): The guaranteed margin of a % Margen Garantizado.

    Returns:
        Decimal | None: The amount; ``None`` for margin-only exceptions, rebates over sales,
        or when a reference it needs is missing.
    """

    if value is None:
        return None
    money = per_unit(value, value_unit, pack_factor) if calc_method in MONEY_METHODS else value
    if calc_method == "pct_costo":
        return cost * value if cost is not None else None
    if calc_method == "pct_precio_lista":
        return list_price * value if list_price is not None else None
    if calc_method in ("valor_unidad", "valor_precio_lista"):
        return money
    if calc_method == "costo_neto":
        return cost - money if cost is not None else None
    if calc_method == "precio_neto":
        if cost is None or guaranteed_margin is None:
            return None
        return cost - money * (ONE - guaranteed_margin)
    return None


def suggested_price(
    calc_method: str,
    amount: Decimal | None,
    list_price: Decimal | None,
    *,
    agreed_price: Decimal | None = None,
) -> Decimal | None:
    """Precio Apoyado Sugerido: the list price minus the supplier's money.

    For % Margen Garantizado the supported price is agreed beforehand (``agreed_price``).
    """

    if calc_method == "precio_neto":
        return agreed_price
    if amount is None or list_price is None:
        return None
    price = list_price - amount
    return price if price > 0 else None


def minimum_price(net_cost: Decimal | None, authorized_margin: Decimal | None) -> Decimal | None:
    """Precio Apoyado Mínimo: the net cost at JDH's authorized minimum margin."""

    return price_at_margin(net_cost, authorized_margin)


def supplier_amount(data: SupportData) -> Decimal | None:
    """The supplier's money per unit with the references stored in the support."""

    cost = per_unit(data.reference_cost, data.value_unit, data.pack_factor)
    listed = per_unit(data.reference_list_price, data.value_unit, data.pack_factor)
    return unit_amount(
        data.calc_method,
        data.supplier_value,
        cost=cost,
        list_price=listed,
        value_unit=data.value_unit,
        pack_factor=data.pack_factor,
        guaranteed_margin=data.target_margin_pct,
    )


def authorized_margin(data: SupportData) -> Decimal | None:
    """JDH's authorized minimum margin: the exception's margin or the one set on a support."""

    if data.supported_margin_pct is not None:
        return data.supported_margin_pct
    if data.calc_method == "margen_minimo":
        return data.min_margin_pct
    return None


def derive(
    data: SupportData,
    dwh_unit_cost: Decimal | None = None,
    product_margin: Decimal | None = None,
    product_pack_factor: Decimal | None = None,
) -> Derived:
    """Compute the derived amounts, prices and margins of a support, per inventory unit.

    The cost is the DWH's (the quoter's rule, refreshed daily) when known; otherwise the
    reference cost stored with the support, brought to units. ``cost_basis`` says which.
    The list price is the cost at the optimal margin of the customer's margin list
    (``product_margin``); without it, the reference list price stored with the support.

    Args:
        data (SupportData): The support inputs.
        dwh_unit_cost (Decimal | None): The DWH cost per inventory unit of its SKU.
        product_margin (Decimal | None): The product's optimal margin for the customer.
        product_pack_factor (Decimal | None): Units per box in the product master; the
            support's own factor is used only when the product is unknown.

    Returns:
        Derived: Whatever can be computed with the references available.
    """

    pack = pack_of(product_pack_factor if product_pack_factor is not None else data.pack_factor)
    data = replace(data, pack_factor=pack)
    basis = None
    cost = per_unit(data.reference_cost, data.value_unit, pack)
    if cost is not None:
        basis = "referencia"
    if dwh_unit_cost is not None and dwh_unit_cost > 0:
        cost, basis = dwh_unit_cost, "dwh"
    margin_of_list = product_margin
    if margin_of_list is None:
        # A support written with its own list/target margin (older sheets); the target of a
        # % Margen Garantizado is the guaranteed margin, not a list margin.
        own_target = None if data.calc_method == "precio_neto" else data.target_margin_pct
        margin_of_list = own_target if own_target is not None else data.list_margin_pct
    listed = price_at_margin(cost, margin_of_list)
    if listed is None:
        listed = per_unit(data.reference_list_price, data.value_unit, pack)

    method = data.calc_method
    amount = unit_amount(
        method,
        data.supplier_value,
        cost=cost,
        list_price=listed,
        value_unit=data.value_unit,
        pack_factor=pack,
        guaranteed_margin=data.target_margin_pct,
    )
    net_cost = cost - amount if cost is not None and amount is not None else None
    if method in ("margen_minimo", "puntos_margen"):
        net_cost = cost
    price = suggested_price(
        method,
        amount,
        listed,
        agreed_price=per_unit(data.supplier_value, data.value_unit, pack),
    )

    min_margin = data.min_margin_pct
    if method == "puntos_margen" and data.supplier_value is not None:
        base = data.list_margin_pct if data.list_margin_pct is not None else product_margin
        min_margin = base - data.supplier_value if base is not None else min_margin

    floor_margin = authorized_margin(data)
    min_price = minimum_price(net_cost, floor_margin)

    def per(value: Decimal | None) -> MoneyPair:
        return MoneyPair(box=None if value is None else value * pack, unit=value)

    return Derived(
        supplier_amount=amount,
        jdh_amount=None,
        net_cost=net_cost,
        pct_on_cost=_div(amount, cost),
        pct_on_list=_div(amount, listed),
        suggested_price=price,
        margin_order=None if price is None else _complement(_div(cost, price)),
        margin_recomposed=None if price is None else _complement(_div(net_cost, price)),
        min_margin_pct=min_margin,
        cost=per(cost),
        list_price=per(listed),
        supplier_amount_per=per(amount),
        net_cost_per=per(net_cost),
        suggested_price_per=per(price),
        cost_basis=basis,
        product_margin_pct=margin_of_list,
        recomposed_list_margin=(
            None if listed is None or net_cost is None else _complement(_div(net_cost, listed))
        ),
        supported_margin_pct=floor_margin,
        supported_price=min_price,
        supported_price_per=per(min_price),
        customer_discount_pct=(
            None if min_price is None or listed is None else _complement(_div(min_price, listed))
        ),
    )
