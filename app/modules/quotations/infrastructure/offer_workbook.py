"""The offer the customer receives, as a one-sheet workbook.

Title, customer and validity, the salesperson when the offer shows them, the items, the
totals when quantities are quoted, and the conditions. Everything comes decided in an
:class:`OfferDocument`: this file only lays it out. It never sees cost, margins, supports
or rule data.
"""

from __future__ import annotations

import io
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.modules.quotations.domain.offer import OfferDocument, long_date

SHEET = "Cotizacion"
TITLE = "Cotización"
# "Imp. consumo" is the liquor consumption tax per unit (blank when the item has none).
ITEM_HEADERS = (
    "Referencia",
    "Descripción",
    "Unidad",
    "Precio sin IVA",
    "IVA %",
    "Imp. consumo",
    "Precio con IVA",
)
VARIATION_HEADER = "Variación"
QUANTITY_HEADERS = ("Cantidad", "Subtotal", "IVA", "Total")
CONDITIONS_TITLE = "Condiciones de la oferta"
_TITLE_FONT = Font(bold=True, size=16)
_BOLD = Font(bold=True)
_HEADER_FILL = PatternFill("solid", start_color="D9E1F2", end_color="D9E1F2")
_MONEY = "#,##0"
_PRICE = "#,##0.##"
_PERCENT = "0.0%"
_FORMATS = {
    "Precio sin IVA": _PRICE,
    "Imp. consumo": _PRICE,
    "Precio con IVA": _PRICE,
    VARIATION_HEADER: _PERCENT,
    "Subtotal": _MONEY,
    "IVA": _MONEY,
    "Total": _MONEY,
}
_WIDTHS = {"Referencia": 14, "Descripción": 48, "Unidad": 9, "IVA %": 8, "Cantidad": 11}


def _number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


class CustomerOfferWorkbook:
    """Writes the customer offer."""

    def write(self, document: OfferDocument) -> bytes:
        book = Workbook()
        sheet = book.active
        sheet.title = SHEET
        sheet.append([TITLE])
        sheet["A1"].font = _TITLE_FONT
        sheet.append([])
        # A business group's offer goes to the group, not to one of its NITs.
        addressee = (
            (
                ("Grupo empresarial", document.business_group),
                ("Clientes", f"{document.group_customers or 0} NIT del grupo"),
            )
            if document.business_group
            else (
                ("Cliente", document.customer_name or document.customer_nit),
                ("NIT", document.customer_nit),
            )
        )
        for label, value in (
            *addressee,
            ("Canal", document.channel),
            ("Fecha", long_date(document.issued)),
            ("Válida hasta", long_date(document.valid_until) if document.valid_until else None),
        ):
            sheet.append([label, value])
            sheet.cell(sheet.max_row, 1).font = _BOLD
        if document.seller is not None:
            sheet.append([])
            sheet.append(["Asesor comercial"])
            sheet.cell(sheet.max_row, 1).font = _BOLD
            for label, value in (
                ("Nombre", document.seller.name),
                ("Correo", document.seller.email),
                ("Teléfono", document.seller.phone),
            ):
                sheet.append([label, value])
        sheet.append([])

        headers = (
            ITEM_HEADERS
            + ((VARIATION_HEADER,) if document.include_variation else ())
            + (QUANTITY_HEADERS if document.quote_quantities else ())
        )
        sheet.append(list(headers))
        header_row = sheet.max_row
        for cell in sheet[header_row]:
            cell.font = _BOLD
            cell.fill = _HEADER_FILL
            cell.alignment = Alignment(wrap_text=True, vertical="center")
        for row in document.rows:
            values = [
                row.reference,
                row.description,
                row.unit,
                _number(row.price),
                _number(row.vat_pct),
                _number(row.liquor_tax),
                _number(row.price_with_vat),
            ]
            if document.include_variation:
                values.append(_number(row.variation_pct))
            if document.quote_quantities:
                values += [
                    _number(row.quantity),
                    _number(row.subtotal),
                    _number(row.vat),
                    _number(row.total),
                ]
            sheet.append(values)
            current = sheet.max_row
            for column, header in enumerate(headers, start=1):
                if header in _FORMATS:
                    sheet.cell(current, column).number_format = _FORMATS[header]

        summary = document.summary
        if document.quote_quantities and summary is not None:
            sheet.append([])
            label_column = len(headers) - 1
            totals: list[tuple[str, Decimal]] = []
            for group in summary.vat_groups:
                totals.append((f"Base IVA {_number(group.vat_pct)} %", group.base))
                totals.append((f"IVA {_number(group.vat_pct)} %", group.vat))
            totals += [
                ("Subtotal", summary.subtotal),
                ("IVA", summary.vat),
                ("Impuesto al consumo", summary.liquor_tax),
                ("Total", summary.total),
            ]
            for label, value in totals:
                sheet.append([None] * (label_column - 1) + [label, _number(value)])
                current = sheet.max_row
                sheet.cell(current, label_column).font = _BOLD
                sheet.cell(current, label_column + 1).number_format = _MONEY
                if label == "Total":
                    sheet.cell(current, label_column + 1).font = _BOLD

        sheet.append([])
        sheet.append([CONDITIONS_TITLE])
        sheet.cell(sheet.max_row, 1).font = _BOLD
        for condition in document.conditions:
            sheet.append([f"• {condition}"])

        for index, header in enumerate(headers, start=1):
            sheet.column_dimensions[get_column_letter(index)].width = _WIDTHS.get(header, 14)
        buffer = io.BytesIO()
        book.save(buffer)
        return buffer.getvalue()


__all__ = [
    "CONDITIONS_TITLE",
    "ITEM_HEADERS",
    "QUANTITY_HEADERS",
    "VARIATION_HEADER",
    "CustomerOfferWorkbook",
]
