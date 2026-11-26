"""The budget workbook: the template a month is filled with, and its reader.

The reader also takes BI's flat file (``Ppto <mes>.xlsx``: REGIONAL, UNIDAD NEGOCIO, EQUIPO,
NOMBRE VENDEDOR CLIENTE, PPTO VENTA, PPTO MARGEN, PPTO # CLIENTES, PPTO RECAUDO, FECHA), where
reps come by name. Columns are found by their header, in any order; a blank cell leaves the
field as it is; total rows (``TOTAL …``) are skipped.
"""

from __future__ import annotations

import io
import unicodedata
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.core.errors import InvalidInputError
from app.modules.commercial_budget.domain.entities import BudgetFileRow, RepRow

CODE = "CÓDIGO VENDEDOR"
NAME = "NOMBRE VENDEDOR"
HEADERS = (
    CODE,
    NAME,
    "REGIONAL",
    "UNIDAD NEGOCIO",
    "EQUIPO",
    "PPTO VENTA",
    "PPTO % MARGEN",
    "PPTO # CLIENTES",
    "PPTO RECAUDO",
    "VENTA MISMO MES AÑO ANTERIOR",
    "VENTA PROMEDIO 3 MESES",
)
_FIELDS = {
    "budget_sales": ("PPTO VENTA", "PPTO", "PRESUPUESTO VENTA"),
    "budget_margin": ("PPTO MARGEN", "PPTO % MARGEN", "PRESUPUESTO MARGEN"),
    "budget_customers": ("PPTO CLIENTES", "PPTO # CLIENTES", "PRESUPUESTO CLIENTES"),
    "budget_collection": ("PPTO RECAUDO", "PRESUPUESTO RECAUDO"),
}
_CODE = ("CODIGO VENDEDOR", "CODIGO", "COD VENDEDOR")
_NAME = ("NOMBRE VENDEDOR", "NOMBRE VENDEDOR CLIENTE", "VENDEDOR")
_HEADER_ROWS = 10
_MAX_ROWS = 5000
_HEAD = PatternFill("solid", fgColor="1F3864")
_INPUT = PatternFill("solid", fgColor="FFF2CC")


def _key(text: Any) -> str:
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    for mark in ("%", "#", ".", "_"):
        plain = plain.replace(mark, " ")
    return " ".join(plain.upper().split())


_ALIASES = {
    **{_key(alias): name for name, aliases in _FIELDS.items() for alias in aliases},
    **{_key(alias): "code" for alias in _CODE},
    **{_key(alias): "name" for alias in _NAME},
}


def budget_template(period: str, rows: Sequence[RepRow]) -> bytes:
    """The month's budget workbook, one row per rep with today's budget."""

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Presupuesto"
    sheet.append(list(HEADERS))
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = _HEAD
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for row in rows:
        budget = row.budget
        sheet.append(
            [
                row.code,
                row.name,
                row.regional,
                row.business_unit,
                row.team,
                _number(budget.budget_sales if budget else None),
                _number(budget.budget_margin if budget else None),
                budget.budget_customers if budget else None,
                _number(budget.budget_collection if budget else None),
                _number(row.last_year.net),
                _number(row.average.net),
            ]
        )
    for index in range(2, sheet.max_row + 1):
        sheet.cell(index, 1).number_format = "@"
        for column in (6, 9, 10, 11):
            sheet.cell(index, column).number_format = "#,##0"
        sheet.cell(index, 7).number_format = "0.0%"
        for column in (6, 7, 8, 9):
            sheet.cell(index, column).fill = _INPUT
    widths = (14, 38, 14, 18, 26, 16, 12, 12, 16, 18, 18)
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = "C2"
    guide = workbook.create_sheet("Instrucciones")
    for line in (
        f"Presupuesto comercial de {period}.",
        "Diligencia las columnas amarillas; la celda vacía deja el valor como está.",
        "PPTO % MARGEN va como porcentaje (18 %) o fracción (0,18).",
        "No cambies el código del vendedor. Una fila sin código se busca por nombre.",
        "Las dos últimas columnas son de referencia y no se cargan.",
    ):
        guide.append([line])
    guide.column_dimensions["A"].width = 100
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _header(sheet) -> tuple[int, dict[str, int]]:
    for index, row in enumerate(
        sheet.iter_rows(min_row=1, max_row=_HEADER_ROWS, values_only=True), start=1
    ):
        columns: dict[str, int] = {}
        for position, value in enumerate(row):
            field = _ALIASES.get(_key(value))
            if field and field not in columns:
                columns[field] = position
        if any(name in columns for name in _FIELDS) and ("code" in columns or "name" in columns):
            return index, columns
    raise InvalidInputError(
        "No se encontró el encabezado: hacen falta el código o el nombre del vendedor y al "
        "menos una columna de presupuesto (PPTO VENTA, PPTO MARGEN, PPTO # CLIENTES, "
        "PPTO RECAUDO)"
    )


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    return text or None


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    text = str(value).strip().replace("$", "").replace(" ", "")
    if not text:
        return None
    percent = text.endswith("%")
    text = text.rstrip("%")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    number = Decimal(text)
    return number / 100 if percent else number


def _value(name: str, raw: Any) -> Decimal | int | None:
    number = _decimal(raw)
    if number is None:
        return None
    if not number.is_finite():
        raise ValueError
    if name == "budget_margin":
        # 0.18 is 18 %; 18 is taken as points (a value above 1 cannot be a fraction).
        if abs(number) > 1:
            number = number / 100
        if abs(number) > 1:
            raise ValueError
        return number
    if number < 0:
        raise ValueError
    if name == "budget_customers":
        if number != number.to_integral_value():
            raise ValueError
        return int(number)
    return number


_LABELS = {
    "budget_sales": "PPTO VENTA",
    "budget_margin": "PPTO MARGEN",
    "budget_customers": "PPTO # CLIENTES",
    "budget_collection": "PPTO RECAUDO",
}


def read_budget_file(content: bytes) -> list[BudgetFileRow]:
    """The rows of a budget workbook (the first sheet with the header).

    Raises:
        InvalidInputError: Not a workbook, no header, or too many rows.
    """

    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - any unreadable file is the user's input
        raise InvalidInputError("No se pudo leer el archivo: no es un .xlsx válido") from exc
    try:
        found = None
        for sheet in workbook.worksheets:
            try:
                found = (sheet, *_header(sheet))
                break
            except InvalidInputError:
                continue
        if found is None:
            _header(workbook.worksheets[0])
        sheet, header, columns = found  # type: ignore[misc]
        rows: list[BudgetFileRow] = []
        for index, values in enumerate(
            sheet.iter_rows(min_row=header + 1, values_only=True), start=header + 1
        ):

            def cell(field: str, values=values) -> Any:
                position = columns.get(field)
                return values[position] if position is not None and position < len(values) else None

            code, name = _text(cell("code")), _text(cell("name"))
            if code is None and name is None:
                continue
            if _key(name).startswith("TOTAL") or _key(code).startswith("TOTAL"):
                continue
            if len(rows) >= _MAX_ROWS:
                raise InvalidInputError(f"El archivo supera {_MAX_ROWS} filas")
            parsed: dict[str, Decimal | int] = {}
            problems = []
            for field in _FIELDS:
                if field not in columns:
                    continue
                raw = cell(field)
                try:
                    value = _value(field, raw)
                except (ValueError, InvalidOperation, ArithmeticError):
                    problems.append(f"{_LABELS[field]} no es un valor válido: {raw}")
                    continue
                if value is not None:
                    parsed[field] = value
            rows.append(BudgetFileRow(index, code, name, parsed, tuple(problems)))
        return rows
    finally:
        workbook.close()


__all__ = ["HEADERS", "budget_template", "read_budget_file"]
