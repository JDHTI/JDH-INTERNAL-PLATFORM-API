"""The supplier increase notice («Comunicado Alza de precios proveedor»), the workbook the area
emails once SIESA has the new costs.

It follows the area's file of 2026-11-18: sheet «Comunicado», columns «Fecha de actualizacion
Sistema», «Referencia», «PROVEEDOR» (``390 - ORGANIZACION ROA FLORHUILA S.A``), «Desc. Item»,
«Variacion %», «Analisis» (SUBIO, BAJO, ESTABLE) and «Precio Nuevo» (the «Lista 1» price of the
history). Two changes: the date reads ``dd/mm/aaaa`` (the area's ``mm-dd-yy`` reads backwards in
Colombia) and the variation keeps two decimals (``2,34 %``, not ``2 %``).
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

SHEET = "Comunicado"
HEADERS = (
    "Fecha de actualizacion Sistema",
    "Referencia",
    "PROVEEDOR",
    "Desc. Item",
    "Variacion %",
    "Analisis",
    "Precio Nuevo",
)
_WIDTHS = (18, 11, 40, 40, 11, 10, 13)
_FORMATS = ("dd/mm/yyyy", "@", "General", "General", "0.00%", "General", '"$" #,##0')


@dataclass(frozen=True)
class NoticeRow:
    """One increase of the notice."""

    loaded_on: date
    sku: str
    supplier: str | None
    description: str | None
    variation_pct: Decimal | None
    analysis: str | None
    price: Decimal | None


def notice_name(day: date) -> str:
    """``Comunicado Alza de precios proveedor 25-11-2026.xlsx``, as the area names it."""

    return f"Comunicado Alza de precios proveedor {day:%d-%m-%Y}.xlsx"


def notice_workbook(rows: Sequence[NoticeRow]) -> bytes:
    """The notice: one row per increase, in the order given."""

    book = Workbook()
    sheet = book.active
    sheet.title = SHEET
    sheet.append(HEADERS)
    fill = PatternFill("solid", fgColor="D9E1F2")
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = fill
    for row in rows:
        sheet.append(
            (
                row.loaded_on,
                row.sku,
                row.supplier,
                row.description,
                None if row.variation_pct is None else float(row.variation_pct),
                row.analysis,
                None if row.price is None else float(row.price),
            )
        )
    for line in sheet.iter_rows(min_row=2):
        for cell, number_format in zip(line, _FORMATS, strict=True):
            cell.number_format = number_format
    for index, width in enumerate(_WIDTHS):
        sheet.column_dimensions[chr(ord("A") + index)].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


__all__ = ["HEADERS", "SHEET", "NoticeRow", "notice_name", "notice_workbook"]
