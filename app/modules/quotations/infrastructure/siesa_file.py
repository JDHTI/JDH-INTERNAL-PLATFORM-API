"""SIESA import file (Impodatos) for a price list update.

Fixed-width ASCII, one record per line, every line ended by CRLF. The layout is the official
dictionary of the importer (``t661_imp_tablas`` / ``t662_imp_campos`` in the SIESA database):

- ``0000`` start and ``9999`` end (18 characters);
- ``0112`` price list header, version 01 (101 characters), only when the update creates the
  list: it goes first and leaves an existing list untouched;
- ``0126`` price, version 03 (254 characters);
- ``0127`` value tax of a price, version 03 (175 characters), right after its ``0126``.

Numbers are zero-filled, money is ``000000000000000.0000`` (15 integers, a dot and 4
decimals), text is left-aligned and space-filled and dates are ``AAAAMMDD``. Optional numeric
fields go as zeros and optional text and date fields as spaces.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.core.errors import InvalidInputError
from app.modules.quotations.domain.export import (
    ExportRow,
    ExportTaxRow,
    NewPriceList,
    PriceListExport,
)

_MONEY_LIMIT = Decimal(10) ** 15
_REPLACE_EXISTING = "1"
_CREATE_ONLY = "0"
# Every list in SIESA is of type 1, with prices before taxes and no suggested price.
_LIST_TYPE = "1"


def money(value: Decimal) -> str:
    """``100`` -> ``000000000000100.0000``."""

    amount = value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    if amount < 0 or amount >= _MONEY_LIMIT:
        raise InvalidInputError(f"El valor {value} no cabe en el plano de SIESA")
    return f"{amount:020.4f}"


def number(value: int, width: int) -> str:
    text = str(value)
    if value < 0 or len(text) > width:
        raise InvalidInputError(f"El número {value} no cabe en {width} posiciones")
    return text.zfill(width)


def text(value: str, width: int, field: str) -> str:
    """Left-aligned and space-filled; SIESA reads plain ASCII only."""

    if len(value) > width:
        raise InvalidInputError(f"{field} «{value}» supera {width} caracteres")
    if not value.isascii():
        raise InvalidInputError(f"{field} «{value}» tiene caracteres no permitidos")
    return value.ljust(width)


def day(value: date) -> str:
    return value.strftime("%Y%m%d")


class SiesaPriceFileWriter:
    """Writes a :class:`PriceListExport` as a SIESA import file."""

    def __init__(self, company: str = "001") -> None:
        """Create the writer.

        Args:
            company (str): SIESA company (``f_cia``), three digits.
        """

        if len(company) != 3 or not company.isdigit():
            raise ValueError("SIESA company must be three digits")
        self.company = company

    def _head(self, number_: int, record: str, version: str) -> str:
        return number(number_, 7) + record + "00" + version + self.company

    def _key(self, list_id: str, sku: str) -> str:
        # Replace an existing price, the list, no internal item id, the SKU, no barcode and
        # no extensions.
        return (
            _REPLACE_EXISTING
            + text(list_id, 3, "La lista de precios")
            + number(0, 7)
            + text(sku, 50, "La referencia")
            + " " * 60
        )

    def price_list(self, number_: int, new_list: NewPriceList) -> str:
        """``0112`` v01: create the list (``0`` in position 19 never overwrites one)."""

        line = (
            self._head(number_, "0112", "01")
            + _CREATE_ONLY
            + text(new_list.price_list_id, 3, "La lista de precios")
            + text(new_list.description, 40, "La descripción de la lista")
            + text(new_list.currency, 3, "La moneda")
            + _LIST_TYPE
            + "0"  # prices without taxes
            + "0"  # no suggested price
            + " " * 3  # no base list
            + money(Decimal(0))  # base factor
            + "0000.0000"  # base precision
            + "0"  # does not apply the base list per item
        )
        assert len(line) == 101, len(line)
        return line

    def price(self, number_: int, row: ExportRow) -> str:
        line = (
            self._head(number_, "0126", "03")
            + self._key(row.price_list_id, row.sku)
            + day(row.activation_date)
            + " " * 8  # inactivation date
            + number(0, 8)  # promotion
            + text(row.unit, 4, "La unidad de medida")
            + money(row.price)
            + money(Decimal(0)) * 3  # minimum, maximum and suggested price
            + number(0, 7)  # loyalty points
        )
        assert len(line) == 254, len(line)
        return line

    def tax(self, number_: int, row: ExportTaxRow) -> str:
        line = (
            self._head(number_, "0127", "03")
            + self._key(row.price_list_id, row.sku)
            + day(row.activation_date)
            + text(row.unit, 4, "La unidad de medida")
            + text(row.tax_key, 4, "La llave de impuesto")
            + money(row.amount)
        )
        assert len(line) == 175, len(line)
        return line

    def write(self, export: PriceListExport) -> bytes:
        """The whole file: start, the new list (if any), each price and its taxes, end."""

        taxes: dict[tuple[str, str], list[ExportTaxRow]] = {}
        for tax in export.taxes:
            taxes.setdefault((tax.price_list_id, tax.sku), []).append(tax)
        lines = [self._head(1, "0000", "01")]
        if export.new_list is not None:
            lines.append(self.price_list(len(lines) + 1, export.new_list))
        for row in export.rows:
            lines.append(self.price(len(lines) + 1, row))
            for tax in taxes.get((row.price_list_id, row.sku), ()):
                lines.append(self.tax(len(lines) + 1, tax))
        lines.append(self._head(len(lines) + 1, "9999", "01"))
        return ("\r\n".join(lines) + "\r\n").encode("ascii")


__all__ = ["SiesaPriceFileWriter", "day", "money", "number", "text"]
