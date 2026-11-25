"""BI's workbook of a quotation: every line with what the platform knows and its costs.

Sheet ``Lineas``: one row per line, the data the table shows plus the costs the advisors do
not see (the cost the rule uses, which source set it, the operation center, the supplier's
quotation, the inventory cost and a quotation left out), so BI can cross-check them against
SIESA. Sheet ``Cotizacion``: the header (customer, list, centers, warehouse, cut-off).

BI's workbook is read-only reference: nothing is uploaded back. The list update template
(2026-11-25) is the same workbook with the columns the advisors see and no costs; they delete
the rows of the references to leave out and upload it back (:mod:`list_template_file`).
The workbook is written in
openpyxl's write-only mode (rows stream to the file, no cell objects kept in memory) and
carries no formulas, validations or conditional formats, so it stays light to write and to
open however many lines it has.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet._write_only import WriteOnlyWorksheet

from app.modules.quotations.domain import vocabulary
from app.modules.quotations.domain.entities import Quotation, QuotationDetail, QuotationLine

LINES_SHEET = "Lineas"
HEADER_SHEET = "Cotizacion"
NATIONAL = "Nacional"
_MONEY = "#,##0"
_PERCENT = "0.00%"
_DAY = "yyyy-mm-dd"
_MOMENT = "yyyy-mm-dd hh:mm"
_BOLD = Font(bold=True)
_BOGOTA = ZoneInfo("America/Bogota")

# (title, width, number format, value of a line)
_COLUMNS: tuple[tuple[str, int, str | None, Any], ...] = (
    ("Referencia", 12, None, lambda line, _: line.sku),
    ("Descripción", 42, None, lambda line, _: line.description),
    ("Categoría", 18, None, lambda line, _: line.category or None),
    ("Proveedor", 28, None, lambda line, _: line.supplier_name),
    ("Unidad", 8, None, lambda line, _: line.unit),
    ("Estado", 9, None, lambda line, _: "Activa" if line.is_active else "Inactiva"),
    ("Origen", 14, None, lambda line, _: vocabulary.ORIGIN_LABELS.get(line.origin, line.origin)),
    ("Ventas en la ventana", 10, None, lambda line, _: line.sales_in_window),
    ("Fecha última venta", 12, _DAY, lambda line, _: line.last_sale_date),
    ("Último precio", 12, _MONEY, lambda line, _: line.last_price),
    ("Precio de lista", 12, _MONEY, lambda line, _: line.list_price),
    ("Margen actual con apoyo", 10, _PERCENT, lambda line, _: line.current_margin_with_support_pct),
    ("Margen objetivo", 10, _PERCENT, lambda line, _: line.target_margin_pct),
    ("Margen mínimo", 10, _PERCENT, lambda line, _: line.min_margin_pct),
    ("Óptimo", 12, _MONEY, lambda line, _: line.optimal_price),
    ("Mínimo", 12, _MONEY, lambda line, _: line.min_price),
    ("Precio nuevo", 12, _MONEY, lambda line, _: line.new_price),
    ("Margen nuevo con apoyo", 10, _PERCENT, lambda line, _: line.new_margin_with_support_pct),
    ("Variación", 10, _PERCENT, lambda line, _: line.variation_pct),
    (
        "Cumplimiento",
        16,
        None,
        lambda line, _: vocabulary.COMPLIANCE_LABELS.get(line.compliance or "", line.compliance),
    ),
    ("Apoyo por unidad", 12, _MONEY, lambda line, _: line.support_amount or None),
    ("Apoyo", 30, None, lambda line, _: line.support.detail if line.support else None),
    ("Impuesto al consumo", 12, _MONEY, lambda line, _: line.value_tax_amount),
    ("IVA %", 7, None, lambda line, _: line.vat_pct),
    ("Cantidad", 10, None, lambda line, _: line.quantity),
    ("Costo más alto", 12, _MONEY, lambda line, _: line.cost),
    (
        "Costo tomado de",
        22,
        None,
        lambda line, _: vocabulary.COST_SOURCE_LABELS.get(line.cost_source or "", line.cost_source),
    ),
    ("C.O. del costo", 22, None, lambda line, names: _center(line.cost_center, names)),
    ("Costo cotización del proveedor", 14, _MONEY, lambda line, _: line.quote_cost),
    ("Costo inventario", 14, _MONEY, lambda line, _: line.inventory_cost),
    ("Cotización descartada", 14, _MONEY, lambda line, _: line.discarded_quote_cost),
    ("Alza pendiente", 14, _MONEY, lambda line, _: line.increase_cost),
    ("Alertas", 60, None, lambda line, _: _problems(line)),
)


# The list update template (2026-11-25): what the advisors see, no costs, in this order. The
# advisors delete the rows of the references to leave out and upload it back.
TEMPLATE_COLUMNS = (
    "Referencia",
    "Descripción",
    "Categoría",
    "Proveedor",
    "Unidad",
    "Estado",
    "Origen",
    "Fecha última venta",
    "Último precio",
    "Precio de lista",
    "Margen actual con apoyo",
    "Precio nuevo",
    "Margen nuevo con apoyo",
    "Variación",
    "Apoyo",
    "Alertas",
)
_TEMPLATE_WIDTHS = {
    "Fecha última venta": 19,
    "Margen actual con apoyo": 24,
    "Margen nuevo con apoyo": 23,
}
_BY_TITLE = {column[0]: column for column in _COLUMNS}
_TEMPLATE = tuple(
    (title, _TEMPLATE_WIDTHS.get(title, width), fmt, read)
    for title, width, fmt, read in (_BY_TITLE[name] for name in TEMPLATE_COLUMNS)
)
SKU_COLUMN = TEMPLATE_COLUMNS[0]
LIST_ROW = "Lista de precios"


def _center(code: str | None, names: Mapping[str, str | None]) -> str | None:
    if not code:
        return None
    name = names.get(code)
    return f"{code} · {name}" if name else code


def _problems(line: QuotationLine) -> str | None:
    labels = [vocabulary.PROBLEM_LABELS.get(code, code) for code in line.problems]
    return "; ".join(labels) or None


def _value(value: Any) -> Any:
    """Decimals as numbers Excel keeps (whole ones as integers)."""

    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _cell(sheet: WriteOnlyWorksheet, value: Any, number_format: str | None = None) -> Any:
    value = _value(value)
    if number_format is None or value is None:
        return value
    cell = WriteOnlyCell(sheet, value=value)
    cell.number_format = number_format
    return cell


def _bold(sheet: WriteOnlyWorksheet, value: str) -> WriteOnlyCell:
    cell = WriteOnlyCell(sheet, value=value)
    cell.font = _BOLD
    return cell


def _centers(quotation: Quotation, names: Mapping[str, str | None]) -> str:
    if not quotation.cost_centers:
        return f"{NATIONAL}: mayor costo entre todas las bodegas"
    parts = []
    for code in quotation.cost_centers:
        text = _center(code, names) or code
        chosen = list(quotation.cost_warehouses.get(code, ()))
        chosen += [f"{item} promocional" for item in quotation.promo_warehouses.get(code, ())]
        parts.append(f"{text} ({', '.join(chosen)})" if chosen else text)
    return ", ".join(parts)


def _header_rows(quotation: Quotation, names: Mapping[str, str | None]) -> list[tuple]:
    return [
        ("Cliente", quotation.customer_name),
        ("NIT", quotation.customer_nit),
        ("Estado", vocabulary.STATUS_LABELS.get(quotation.status, quotation.status)),
        ("Lista de precios", quotation.price_list_id),
        ("Nombre de la lista", quotation.price_list_name),
        ("Canal", quotation.channel),
        ("C.O. del costo", _centers(quotation, names)),
        ("Bodega", quotation.stock_source),
        # The liquor tax one (Nacional or Costa), not the center's regional.
        (
            "Regional impuesto licores",
            vocabulary.REGIONAL_LABELS.get(quotation.regional, quotation.regional),
        ),
        ("Costo al corte", quotation.cost_cut_date),
        ("Armada", quotation.built_at),
        ("Referencias", quotation.line_count),
    ]


class QuotationLinesWorkbook:
    """Writes BI's workbook of a quotation's lines and costs, or the list update template."""

    def write(
        self,
        detail: QuotationDetail,
        center_names: Mapping[str, str | None],
        template: bool = False,
    ) -> bytes:
        """The workbook; with ``template`` only :data:`TEMPLATE_COLUMNS` (no costs) and a
        filter on the header, for the advisors to delete rows and upload it back."""

        columns = _TEMPLATE if template else _COLUMNS
        book = Workbook(write_only=True)
        lines = book.create_sheet(LINES_SHEET)
        for index, (_, width, _, _) in enumerate(columns, start=1):
            lines.column_dimensions[get_column_letter(index)].width = width
        lines.freeze_panes = "B2"
        if template:
            last = get_column_letter(len(columns))
            lines.auto_filter.ref = f"A1:{last}{len(detail.lines) + 1}"
        lines.append([_bold(lines, title) for title, _, _, _ in columns])
        for line in detail.lines:
            lines.append(
                [_cell(lines, read(line, center_names), fmt) for _, _, fmt, read in columns]
            )

        header = book.create_sheet(HEADER_SHEET)
        header.column_dimensions["A"].width = 20
        header.column_dimensions["B"].width = 60
        for label, value in _header_rows(detail.quotation, center_names):
            fmt = None
            if isinstance(value, datetime):
                # Excel has no time zones: the Bogotá wall clock.
                value, fmt = value.astimezone(_BOGOTA).replace(tzinfo=None), _MOMENT
            elif isinstance(value, date):
                fmt = _DAY
            header.append([_bold(header, label), _cell(header, value, fmt)])

        buffer = io.BytesIO()
        book.save(buffer)
        return buffer.getvalue()


__all__ = [
    "HEADER_SHEET",
    "LINES_SHEET",
    "LIST_ROW",
    "SKU_COLUMN",
    "TEMPLATE_COLUMNS",
    "QuotationLinesWorkbook",
]
