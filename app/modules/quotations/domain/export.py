"""What goes into a price list update, independent of the file format."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class ExportRow:
    """One price of the update (sheet ``Precios`` / record ``0126``)."""

    price_list_id: str
    sku: str
    activation_date: date
    unit: str
    price: Decimal


@dataclass(frozen=True)
class ExportTaxRow:
    """One value tax of the update (sheet ``Impuestos en valor`` / record ``0127``)."""

    price_list_id: str
    sku: str
    activation_date: date
    unit: str
    tax_key: str
    amount: Decimal


@dataclass(frozen=True)
class PriceListExport:
    """The rows of a price list update; each tax row follows the price of its SKU."""

    rows: tuple[ExportRow, ...]
    taxes: tuple[ExportTaxRow, ...] = ()
