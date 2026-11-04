"""The ``ACTUALIZACION LISTAS PRECIOS`` workbook SIESA's Excel importer reads.

Format of the area's ``Plano Listas de Precios`` (skill ``lista-precios``): the list as text
(``'001``, not 1), the reference as a number when it is one, the date as the whole number
``AAAAMMDD`` with format ``0`` (never a date: it would show as a serial), the price as a
number, and Calibri Light 10 in every cell.
"""

from __future__ import annotations

import io
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font

from app.modules.quotations.domain.export import PriceListExport
from app.modules.quotations.infrastructure.siesa_file import day

PRICE_SHEET = "Precios"
TAX_SHEET = "Impuestos en valor"
PRICE_HEADERS = ("LISTA DE PRECIOS", "REFERENCIA", "AAAAMMDD", "UNIDAD_MEDIDA", "PRECIO DE LISTA")
TAX_HEADERS = (
    "LISTA DE PRECIOS",
    "REFERENCIA",
    "AAAAMMDD",
    "UNIDAD_MEDIDA",
    "LLAVE IMPUESTO",
    "VALOR IMPUESTO",
)
FONT = Font(name="Calibri Light", size=10)


def _reference(sku: str) -> int | str:
    return int(sku) if sku.isdigit() and not sku.startswith("0") else sku


def _amount(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def _sheet(workbook: Workbook, title: str, headers: tuple[str, ...], rows: list[tuple]) -> None:
    sheet = workbook.create_sheet(title)
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    sheet.column_dimensions["C"].number_format = "0"
    for cells in sheet.iter_rows():
        for cell in cells:
            cell.font = FONT
            if cell.column == 1:
                cell.number_format = "@"
            elif cell.column == 3 and cell.row > 1:
                cell.number_format = "0"


class PriceListWorkbookWriter:
    """Writes a price list update as the two-sheet workbook of the template."""

    def write(self, export: PriceListExport) -> bytes:
        workbook = Workbook()
        workbook.remove(workbook.active)
        _sheet(
            workbook,
            PRICE_SHEET,
            PRICE_HEADERS,
            [
                (
                    row.price_list_id,
                    _reference(row.sku),
                    int(day(row.activation_date)),
                    row.unit,
                    _amount(row.price),
                )
                for row in export.rows
            ],
        )
        _sheet(
            workbook,
            TAX_SHEET,
            TAX_HEADERS,
            [
                (
                    tax.price_list_id,
                    _reference(tax.sku),
                    int(day(tax.activation_date)),
                    tax.unit,
                    tax.tax_key,
                    _amount(tax.amount),
                )
                for tax in export.taxes
            ],
        )
        buffer = io.BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()


__all__ = ["PRICE_HEADERS", "PRICE_SHEET", "TAX_HEADERS", "TAX_SHEET", "PriceListWorkbookWriter"]
