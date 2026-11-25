"""The files of a SIESA load: the supplier quotation import file and its workbook.

**Import file (Impodatos).** Fixed-width ASCII, one record per line, every line ended by CRLF,
as the price list file of the quoter (``quotations/infrastructure/siesa_file.py``):

- ``0000`` start and ``9999`` end (18 characters);
- ``0212`` supplier quotation, version 02 (200 characters), one per increase:

  ========  =====  ===========================================================
  Position  Size   Field
  ========  =====  ===========================================================
  1         7      Record number
  8         4      ``0212``
  12        2      Subtype ``00``
  14        2      Version ``02``
  16        3      Company ``001``
  19        1      ``1``: update the quotation when it exists
  20        15     Supplier NIT
  35        3      Supplier branch (``001``)
  38        3      Currency (``COP``)
  41        7      Item id: zeros, the item goes by its reference
  48        50     Reference
  98        60     Barcode and the two extensions: blank
  158       8      Activation date ``AAAAMMDD``
  166       4      Unit (``UNID``, ``KL``)
  170       20     Unit price ``000000000000000.0000``
  190       3      Delivery days
  193       8      End date ``20991231``
  ========  =====  ===========================================================

Laid out from the area's file of 2026-11-18 (``34524H11M48…txt``, eight quotations) and checked
against what SIESA kept of it in the warehouse: supplier, branch, unit, price, activation date,
delivery days (5) and end date (2099-12-31) of each one match. The file is generated again
byte for byte in the tests.

**Workbook** (``IncrementoCostos Entradas``): the same quotations as text, as the area keeps
them: sheet «Encabezado Cotizaciones », columns ``ID_TERCERO``, ``F212_ID_SUCURSAL``,
``REFERENCIA_ITEM``, ``AAAAMMDD``, ``UM`` (four characters) and ``PRECIO`` (two decimals).
"""

from __future__ import annotations

import io
from collections.abc import Sequence

from openpyxl import Workbook
from openpyxl.styles import Font

from app.modules.commercial_masters.domain.siesa_loads import OPEN_END, LoadRow
from app.modules.quotations.infrastructure.siesa_file import day, money, number, text

_UPDATE_EXISTING = "1"
_COMPANY = "001"
SHEET = "Encabezado Cotizaciones "
HEADERS = ("ID_TERCERO", "F212_ID_SUCURSAL", "REFERENCIA_ITEM", "AAAAMMDD", "UM", "PRECIO")


def _head(number_: int, record: str, version: str) -> str:
    return number(number_, 7) + record + "00" + version + _COMPANY


def quotation_line(number_: int, row: LoadRow) -> str:
    """The ``0212`` v02 record of one increase (200 characters)."""

    line = (
        _head(number_, "0212", "02")
        + _UPDATE_EXISTING
        + text(row.nit, 15, "El NIT del proveedor")
        + text(row.branch, 3, "La sucursal del proveedor")
        + text(row.currency, 3, "La moneda")
        + number(0, 7)
        + text(row.sku, 50, "La referencia")
        + " " * 60
        + day(row.activation_date)
        + text(row.unit, 4, "La unidad de medida")
        + money(row.price)
        + number(row.delivery_days, 3)
        + day(OPEN_END)
    )
    assert len(line) == 200, len(line)
    return line


def quotation_file(rows: Sequence[LoadRow]) -> bytes:
    """The whole import file: start, one ``0212`` per increase, end."""

    lines = [_head(1, "0000", "01")]
    for row in rows:
        lines.append(quotation_line(len(lines) + 1, row))
    lines.append(_head(len(lines) + 1, "9999", "01"))
    return ("\r\n".join(lines) + "\r\n").encode("ascii")


def quotation_workbook(rows: Sequence[LoadRow]) -> bytes:
    """The workbook of the same quotations, every cell as text."""

    book = Workbook()
    sheet = book.active
    sheet.title = SHEET
    sheet.append(HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append(
            (
                row.nit,
                row.branch,
                row.sku,
                day(row.activation_date),
                row.unit.ljust(4),
                f"{row.price:.2f}",
            )
        )
    for line in sheet.iter_rows():
        for cell in line:
            cell.number_format = "@"
    for column, width in zip("ABCDEF", (14, 18, 18, 11, 6, 14), strict=True):
        sheet.column_dimensions[column].width = width
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


__all__ = ["HEADERS", "SHEET", "quotation_file", "quotation_line", "quotation_workbook"]
