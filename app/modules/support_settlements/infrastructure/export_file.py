"""The settlement workbook sent to a supplier: summary by reference, every sale line and what
was not settled."""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from app.modules.support_settlements.domain.entities import (
    ITEM_STATUS_LABELS,
    SALE_FLAG_LABELS,
    SETTLED,
)
from app.modules.support_settlements.domain.rules import margin, recomposed_margin

_MONEY = "#,##0"
_PCT = "0.00%"
_QTY = "#,##0.##"


def _number(value: Any) -> float | None:
    return None if value is None else float(value)


def _sheet(book: Workbook, title: str, headers: Sequence[str], first: bool = False):
    sheet = book.active if first else book.create_sheet(title)
    sheet.title = title
    sheet.append(list(headers))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"
    return sheet


def _formats(sheet, formats: Mapping[int, str]) -> None:
    for column, fmt in formats.items():
        letter = get_column_letter(column)
        for cell in sheet[letter][1:]:
            cell.number_format = fmt
        sheet.column_dimensions[letter].width = 16


def settlement_workbook(
    period: str,
    status: str,
    references: Sequence[Mapping[str, Any]],
    sales: Sequence[Mapping[str, Any]],
) -> bytes:
    """The workbook of a month (one supplier or all)."""

    book = Workbook()
    summary = _sheet(
        book,
        "Resumen",
        [
            "Periodo",
            "Estado",
            "Proveedor",
            "Nombre proveedor",
            "Referencia",
            "Descripción",
            "Cantidad",
            "Venta",
            "Costo",
            "Margen",
            "Apoyo a reconocer",
            "Margen recompuesto",
            "Apoyos no liquidados",
            "Líneas con apoyo",
        ],
        first=True,
    )
    totals = [Decimal(0)] * 4
    for ref in references:
        net, cost, amount = ref["net"], ref["cost"], ref["amount"]
        totals = [
            totals[0] + net,
            totals[1] + cost,
            totals[2] + amount,
            totals[3] + ref["flagged_amount"],
        ]
        summary.append(
            [
                period,
                status,
                ref["supplier_code"],
                ref["supplier_name"],
                ref["sku"],
                ref["description"],
                _number(ref["quantity"]),
                _number(net),
                _number(cost),
                _number(margin(net, cost)),
                _number(amount),
                _number(recomposed_margin(net, cost, amount)),
                _number(ref["flagged_amount"]),
                ref["supported_lines"],
            ]
        )
    summary.append(
        [
            "Total",
            None,
            None,
            None,
            None,
            None,
            None,
            _number(totals[0]),
            _number(totals[1]),
            _number(margin(totals[0], totals[1])),
            _number(totals[2]),
            _number(recomposed_margin(totals[0], totals[1], totals[2])),
            _number(totals[3]),
            None,
        ]
    )
    summary[summary.max_row][0].font = Font(bold=True)
    _formats(summary, {7: _QTY, 8: _MONEY, 9: _MONEY, 10: _PCT, 11: _MONEY, 12: _PCT, 13: _MONEY})

    detail = _sheet(
        book,
        "Detalle ventas",
        [
            "Fecha",
            "Documento",
            "C.O",
            "Regional",
            "Unidad de negocio",
            "Canal",
            "Ciudad",
            "Vendedor",
            "NIT",
            "Sucursal",
            "Cliente",
            "Grupo empresarial",
            "Proveedor",
            "Referencia",
            "Descripción",
            "Bodega",
            "Devolución",
            "Motivo",
            "Cantidad",
            "Venta",
            "Costo",
            "Costo base unitario",
            "Fuente del costo",
            "Apoyo a reconocer",
            "Margen",
            "Margen recompuesto",
            "Marcas",
            "Cómo se calculó",
        ],
    )
    pending = _sheet(
        book,
        "No liquidados",
        [
            "Fecha",
            "Documento",
            "NIT",
            "Cliente",
            "Referencia",
            "Estado",
            "Valor calculado",
            "Cómo se calculó",
            "Apoyo",
        ],
    )
    for sale in sales:
        net, cost, amount = sale["net"], sale["cost"], sale["amount"]
        detail.append(
            [
                sale["sale_date"],
                sale["document"],
                sale["co_name"] or sale["co"],
                sale["regional"],
                sale["business_unit"],
                sale["channel"],
                sale["city"],
                sale["salesperson"],
                sale["nit"],
                sale["branch_id"],
                sale["customer_name"],
                sale["business_group"],
                sale["supplier_name"] or sale["supplier_code"],
                sale["sku"],
                sale["description"],
                sale["warehouse"],
                "Sí" if sale["is_return"] else "No",
                sale["reason"],
                _number(sale["quantity"]),
                _number(net),
                _number(cost),
                _number(sale["base_unit_cost"]),
                "Cotización" if sale["cost_source"] == "cotizacion" else "Factura",
                _number(amount),
                _number(margin(net, cost)),
                _number(recomposed_margin(net, cost, amount)),
                "; ".join(SALE_FLAG_LABELS.get(flag, flag) for flag in sale["flags"] or ()),
                " | ".join(item["detail"] for item in sale["items"]),
            ]
        )
        for item in sale["items"]:
            if item["status"] != SETTLED:
                pending.append(
                    [
                        sale["sale_date"],
                        sale["document"],
                        sale["nit"],
                        sale["customer_name"],
                        sale["sku"],
                        ITEM_STATUS_LABELS.get(item["status"], item["status"]),
                        _number(item["amount"]),
                        item["detail"],
                        str(item["support_id"]),
                    ]
                )
    _formats(detail, {19: _QTY, 20: _MONEY, 21: _MONEY, 22: _MONEY, 24: _MONEY, 25: _PCT, 26: _PCT})
    _formats(pending, {7: _MONEY})
    for sheet in (detail, pending):
        sheet.column_dimensions["A"].width = 12
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


__all__ = ["settlement_workbook"]
