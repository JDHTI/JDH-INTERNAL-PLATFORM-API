"""The JDH employee price list: the MDE1 cost at the employee margin.

    price          = MDE1 average cost / (1 - margin_employee), in whole pesos (half up)
    price with VAT = price * (1 + VAT %), in whole pesos (half up)

The VAT rate is read live from SIESA's product (``iva_venta_pct``), with 0 % for the tax groups
without VAT (see :func:`sale_vat`).

The cost is the average inventory cost of warehouse MDE1 in SIESA's morning photo
(``consumo.inventario_fecha``), per inventory unit and before VAT, rounded to pesos as the
quoter reads it; a cost of one peso or less is a placeholder and does not price. A product
without a margin of its own takes the 13 % default.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

EMPLOYEE_WAREHOUSE = "MDE1"
DEFAULT_EMPLOYEE_MARGIN = Decimal("0.13")
_ONE = Decimal(1)
_HUNDRED = Decimal(100)
_PESO = Decimal(1)


def employee_price(cost: Decimal | None, margin: Decimal | None) -> Decimal | None:
    """``cost / (1 - margin)`` in whole pesos; ``None`` without a usable cost.

    Args:
        cost (Decimal | None): MDE1 unit cost before VAT.
        margin (Decimal | None): Employee margin as a fraction; ``None`` takes the default.

    Returns:
        Decimal | None: The unit price before VAT.
    """

    if cost is None or cost <= _ONE:
        return None
    rate = DEFAULT_EMPLOYEE_MARGIN if margin is None else margin
    if not Decimal(0) <= rate < _ONE:
        return None
    return (cost / (_ONE - rate)).quantize(_PESO, ROUND_HALF_UP)


def sale_vat(vat_pct: Decimal | None, tax_group: str | None) -> Decimal | None:
    """The sale VAT rate of a SKU in percentage points (19, 5 or 0); ``None`` when unknown.

    SIESA leaves ``iva_venta_pct`` empty for the products of a tax group without VAT
    (``0024 SIN IVA, RFFTE COMPRAS 2.5%, RTICA 1%``, ``0000 SIN IVA, SIN RETENCIONES``...): they
    are invoiced at 0 %. An empty rate in a group that does carry VAT (``1640 IVA 19%, SIN
    RETENCIONES``) is a gap in the data, not a 0 %.
    """

    if vat_pct is not None:
        return vat_pct
    if tax_group and tax_group.strip().upper().startswith("SIN IVA"):
        return Decimal(0)
    return None


def with_vat(price: Decimal | None, vat_pct: Decimal | None) -> Decimal | None:
    """The price plus its VAT (``vat_pct`` in percentage points, 19 = 19 %)."""

    if price is None:
        return None
    rate = vat_pct if vat_pct is not None and vat_pct > 0 else Decimal(0)
    return (price * (_ONE + rate / _HUNDRED)).quantize(_PESO, ROUND_HALF_UP)


@dataclass(frozen=True)
class WarehouseCost:
    """The average cost of a SKU in one warehouse, as the morning photo has it, with the
    product's sale VAT and tax group as SIESA has them today."""

    sku: str
    cost: Decimal
    snapshot_date: date | None
    vat_pct: Decimal | None = None
    tax_group_id: str | None = None
    tax_group: str | None = None


@dataclass(frozen=True)
class EmployeePriceProduct:
    """The product fields the employee list needs from the master."""

    sku: str
    description: str | None
    brand: str | None
    inventory_unit: str | None
    vat_pct: Decimal | None
    margin: Decimal | None
    is_active: bool


@dataclass(frozen=True)
class EmployeePrice:
    """One line of the employee price list.

    ``vat_pct`` is 19, 5 or 0 (a group without VAT); ``None`` only when SIESA's tax group
    carries VAT and the rate is missing. ``tax_group_id`` and ``tax_group`` are SIESA's.
    """

    sku: str
    description: str | None
    brand: str | None
    inventory_unit: str | None
    cost: Decimal
    margin: Decimal
    price: Decimal
    vat_pct: Decimal | None
    price_with_vat: Decimal
    tax_group_id: str | None = None
    tax_group: str | None = None


@dataclass(frozen=True)
class EmployeePriceList:
    """The whole list: every active product with an MDE1 cost.

    ``without_cost`` counts the active products left out because MDE1 has no usable cost
    for them (no stock row there, or a placeholder cost). ``unknown_vat`` counts the lines
    whose VAT rate is unknown (``vat_pct`` ``None``).
    """

    warehouse: str
    generated_at: datetime
    snapshot_date: date | None
    items: tuple[EmployeePrice, ...]
    without_cost: int
    unknown_vat: int = 0
