"""Unit of a cost: per minimum selling unit or per box.

The workbook mixes both and most sheets do not say which. When a sheet does not declare
it, the unit is inferred by comparing the sheet's cost with the warehouse's current
average cost of one unit (``products.avg_unit_cost``): a cost close to it is per unit,
a cost close to it times the pack factor is per box. Anything in between is left
undecided rather than guessed.
"""

from __future__ import annotations

from decimal import Decimal
from math import log

# A workbook cost is a negotiated list/cost, the warehouse cost is a moving average, and
# the ICUI tax is not separable: allow a wide band around each hypothesis.
TOLERANCE = Decimal("2.0")


def infer_value_unit(
    reference_cost: Decimal | None,
    unit_cost: Decimal | None,
    pack_factor: Decimal | None,
) -> str | None:
    """Decide whether ``reference_cost`` is per unit (``unidad``) or per box (``caja``).

    Args:
        reference_cost (Decimal | None): Cost written in the workbook.
        unit_cost (Decimal | None): Warehouse average cost of one minimum unit.
        pack_factor (Decimal | None): Units per box.

    Returns:
        str | None: ``unidad``, ``caja``, or ``None`` when it cannot be told: no cost or
        no warehouse cost, a box of one unit (both are the same), or a ratio that is far
        from both one unit and one box.
    """

    if not reference_cost or not unit_cost or reference_cost <= 0 or unit_cost <= 0:
        return None
    if pack_factor is None or pack_factor <= 1:
        return None
    ratio = reference_cost / unit_cost
    near_unit = abs(log(float(ratio)))
    near_box = abs(log(float(ratio / pack_factor)))
    band = log(float(TOLERANCE))
    if min(near_unit, near_box) > band:
        return None
    # A pack of 2 or 3 leaves the two hypotheses inside each other's band: nearest wins.
    return "unidad" if near_unit <= near_box else "caja"


def to_box_and_unit(
    value: Decimal | None, value_unit: str | None, pack_factor: Decimal | None
) -> tuple[Decimal | None, Decimal | None]:
    """Express a money value per box and per unit.

    Args:
        value (Decimal | None): Amount in the support's own unit.
        value_unit (str | None): ``caja`` or ``unidad``; anything else is not convertible.
        pack_factor (Decimal | None): Units per box.

    Returns:
        tuple[Decimal | None, Decimal | None]: ``(per box, per unit)``; the side that needs
        the pack factor is ``None`` when it is missing.
    """

    if value is None:
        return None, None
    if value_unit == "caja":
        unit = value / pack_factor if pack_factor else None
        return value, unit
    if value_unit == "unidad":
        box = value * pack_factor if pack_factor else None
        return box, value
    return None, None
