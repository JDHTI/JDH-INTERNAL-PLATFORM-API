"""The quotation as a workbook the advisor edits in bulk and uploads back.

Sheet ``Cotizacion``: one row per SKU with what the platform knows (last price, cost,
support, margins, minimum and optimal price) and two editable columns, ``Precio nuevo`` and
``Margen nuevo``. Only ``Referencia`` and those two are read back; the rest is reference.
Sheet ``Control`` carries the quotation id so a file is not uploaded to another quotation.
"""

from __future__ import annotations

import io
import re
import zipfile
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import openpyxl
from openpyxl.styles import Font, PatternFill
from openpyxl.utils.exceptions import InvalidFileException

from app.core.errors import InvalidInputError
from app.modules.commercial_masters.domain.margins import normalize
from app.modules.quotations.domain.entities import QuotationDetail, QuotationLine, TemplateRow

SHEET = "Cotizacion"
CONTROL_SHEET = "Control"
HEADERS = (
    "Referencia",
    "Descripción",
    "Unidad",
    "Estado",
    "Proveedor",
    "Último precio",
    "Costo",
    "Apoyo por unidad",
    "Margen actual",
    "Margen objetivo",
    "Precio mínimo",
    "Precio óptimo",
    "Precio nuevo",
    "Margen nuevo",
    "Alerta",
)
_PRICE_COLUMN = HEADERS.index("Precio nuevo") + 1
_MARGIN_COLUMN = HEADERS.index("Margen nuevo") + 1
_PERCENT_COLUMNS = {HEADERS.index(name) + 1 for name in ("Margen actual", "Margen objetivo")}
_MONEY_COLUMNS = {
    HEADERS.index(name) + 1
    for name in ("Último precio", "Costo", "Apoyo por unidad", "Precio mínimo", "Precio óptimo")
}
_EDITABLE = PatternFill("solid", start_color="FFF2CC", end_color="FFF2CC")
_BOLD = Font(bold=True)
_SKU = {"referencia", "sku"}
_PRICE = {"precio nuevo"}
_MARGIN = {"margen nuevo"}
_SCAN_ROWS = 10
_THOUSANDS = re.compile(r"^\d{1,3}(\.\d{3})+(,\d+)?$")
MAX_ROWS = 5000


def _number(value: Decimal | None) -> float | int | None:
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def _alert(line: QuotationLine) -> str | None:
    if not line.is_active:
        return "Referencia inactiva: reemplázala"
    if line.below_target:
        return "Margen actual bajo el objetivo: revisar y actualizar"
    return None


def _row(line: QuotationLine) -> list[Any]:
    return [
        line.sku,
        line.description,
        line.unit,
        "Activa" if line.is_active else "Inactiva",
        line.supplier_name,
        _number(line.last_price),
        _number(line.cost),
        _number(line.support_amount) or None,
        _number(line.current_margin_with_support_pct),
        _number(line.target_margin_pct),
        _number(line.min_price),
        _number(line.optimal_price),
        _number(line.new_price),
        _number(line.new_margin_with_support_pct),
        _alert(line),
    ]


def parse_price(value: Any) -> Decimal | None:
    """A price cell: blank is ``None``; ``53958``, ``"53.958"`` or ``"$ 53.958"`` are pesos.

    Raises:
        ValueError: If it is not a number above zero.
    """

    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(value)
    if isinstance(value, int | float | Decimal):
        number = Decimal(str(value))
    else:
        text = str(value).replace("$", "").replace(" ", "").strip()
        if _THOUSANDS.match(text):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", ".")
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(value) from exc
    if not number.is_finite() or number <= 0:
        raise ValueError(value)
    return number


def parse_margin(value: Any) -> Decimal | None:
    """A margin cell: a fraction (``0.3``) or points (``30`` or ``30 %``); blank is ``None``.

    Raises:
        ValueError: If it is not a number from 0 to under 100 %.
    """

    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(value)
    text = str(value).replace("%", "").replace(" ", "").replace(",", ".").strip()
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(value) from exc
    if isinstance(value, str) and "%" in value or number >= 1:
        number = number / 100
    if not number.is_finite() or number < 0 or number >= 1:
        raise ValueError(value)
    return number.quantize(Decimal("0.000001"))


def _cell(row: tuple[Any, ...], column: int | None) -> Any:
    return row[column] if column is not None and column < len(row) else None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    return text or None


class QuotationTemplateFile:
    """Writes the quotation workbook and reads the advisor's prices and margins back."""

    def write(self, detail: QuotationDetail) -> bytes:
        quotation = detail.quotation
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = SHEET
        sheet.append(list(HEADERS))
        for cell in sheet[1]:
            cell.font = _BOLD
        for line in detail.lines:
            sheet.append(_row(line))
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                if cell.column in _PERCENT_COLUMNS or cell.column == _MARGIN_COLUMN:
                    cell.number_format = "0.00%"
                elif cell.column in _MONEY_COLUMNS or cell.column == _PRICE_COLUMN:
                    cell.number_format = "#,##0"
                if cell.column in (_PRICE_COLUMN, _MARGIN_COLUMN):
                    cell.fill = _EDITABLE
        sheet.freeze_panes = "B2"
        for column, width in zip("ABCDEFGHIJKLMNO", (12, 42, 8, 9, 30), strict=False):
            sheet.column_dimensions[column].width = width

        control = book.create_sheet(CONTROL_SHEET)
        for item in (
            ("Cotización", str(quotation.id)),
            ("Cliente", quotation.customer_name),
            ("NIT", quotation.customer_nit),
            ("Lista", quotation.price_list_id),
            ("Instrucciones", "Escribe el Precio nuevo o el Margen nuevo de cada referencia."),
            ("", "Si cambias las dos, manda el precio. Un margen es 0,30 o 30 %."),
            ("", "Deja las dos vacías para quitar el precio nuevo de esa referencia."),
            (
                "",
                "Una referencia que no está en la cotización se agrega (si existe y está activa).",
            ),
            ("", "Las demás columnas son de consulta y no se leen."),
        ):
            control.append(list(item))
        buffer = io.BytesIO()
        book.save(buffer)
        return buffer.getvalue()

    def quotation_id(self, content: bytes) -> UUID | None:
        """The quotation the file was downloaded from, if it says."""

        book = self._load(content)
        try:
            if CONTROL_SHEET not in book.sheetnames:
                return None
            for row in book[CONTROL_SHEET].iter_rows(max_row=5, values_only=True):
                if row and normalize(row[0]) == "cotizacion" and row[1]:
                    try:
                        return UUID(str(row[1]))
                    except ValueError:
                        return None
            return None
        finally:
            book.close()

    @staticmethod
    def _load(content: bytes):
        try:
            return openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except (InvalidFileException, zipfile.BadZipFile, OSError, KeyError, ValueError) as exc:
            raise InvalidInputError("El archivo no es un libro de Excel válido") from exc

    def read(self, filename: str, content: bytes) -> list[TemplateRow]:
        """The rows with a reference, with the price and margin cells parsed.

        Raises:
            InvalidInputError: Not an ``.xlsx``, or no header with ``Referencia`` and
                ``Precio nuevo`` or ``Margen nuevo``.
        """

        if not filename.lower().endswith((".xlsx", ".xlsm")):
            raise InvalidInputError("Sube la plantilla en Excel (.xlsx)")
        book = self._load(content)
        try:
            sheet = book[SHEET] if SHEET in book.sheetnames else book.worksheets[0]
            rows = list(sheet.iter_rows(values_only=True))
        finally:
            book.close()
        header = None
        for index, row in enumerate(rows[:_SCAN_ROWS]):
            names = {normalize(cell): column for column, cell in enumerate(row) if cell}
            sku = next((names[name] for name in _SKU if name in names), None)
            price = next((names[name] for name in _PRICE if name in names), None)
            margin = next((names[name] for name in _MARGIN if name in names), None)
            if sku is not None and (price is not None or margin is not None):
                header = (index, sku, price, margin)
                break
        if header is None:
            raise InvalidInputError(
                "No se encontró el encabezado: se necesita Referencia y Precio nuevo o Margen nuevo"
            )
        start, sku_column, price_column, margin_column = header
        result: list[TemplateRow] = []
        for number, row in enumerate(rows[start + 1 :], start=start + 2):
            sku = _text(_cell(row, sku_column))
            if sku is None:
                continue
            if len(result) >= MAX_ROWS:
                raise InvalidInputError(f"La plantilla admite máximo {MAX_ROWS} referencias")
            price_cell = _cell(row, price_column)
            margin_cell = _cell(row, margin_column)
            problems = []
            try:
                price = parse_price(price_cell)
            except ValueError:
                price, problems = None, [f"Precio nuevo inválido: {price_cell!r}"]
            try:
                margin = parse_margin(margin_cell)
            except ValueError:
                margin = None
                problems.append(f"Margen nuevo inválido: {margin_cell!r}")
            result.append(TemplateRow(number, sku, price, margin, "; ".join(problems) or None))
        return result


__all__ = ["QuotationTemplateFile", "parse_margin", "parse_price"]
