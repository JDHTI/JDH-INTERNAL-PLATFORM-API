"""Reads and writes the purchasing area's price increase workbook (``Acumulado Alza Precios``).

The history is exported in the workbook's 29 columns (:func:`history_workbook`), kept in the
area's sheet and uploaded back (2026-11-25): an export uploaded as it is changes nothing.

The header row is found by name among the first rows, in any column order; only the
columns of the increase are read. The product's own columns (``Nit``, ``Proveedor``,
``Desc. item``, ``Unidad de Empaque``, ``Estado el Item``, ``Categoria``, ``Grupo
Impositivo``, ``Descuento Regular``) are ignored: the product master keeps them.

openpyxl opens the file read-only (rows stream, no cell objects kept), so a 50 000-row
history reads in seconds; the service runs it in a worker thread.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

import openpyxl
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font
from openpyxl.utils.exceptions import InvalidFileException

from app.core.errors import InvalidInputError
from app.modules.commercial_masters.domain.margins import normalize
from app.modules.commercial_masters.domain.price_increases import (
    CellError,
    CellNote,
    ExportRow,
    FileRow,
    PriceIncreaseValues,
    clean_text,
    parse_date,
    parse_flag,
    parse_money,
    parse_ratio,
    parse_status,
    status_of,
)

HEADER_SCAN_ROWS = 10
MAX_ROWS = 200_000
STATUS_WORDS = {"subio": "SUBIO", "bajo": "BAJO", "estable": "ESTABLE"}

# Normalized header -> (field, reader, label for the report).
_READERS: dict[str, tuple[str, Callable[[Any], Any], str]] = {
    "fecha de notificacion": ("notified_on", parse_date, "Fecha de notificación"),
    "fecha de actualizacion sistema": (
        "system_updated_on",
        parse_date,
        "Fecha de actualización sistema",
    ),
    "fecha de incremento": ("increased_on", parse_date, "Fecha de incremento"),
    "sku proveedor": ("supplier_sku", clean_text, "SKU proveedor"),
    "codigo de barras ean 13": ("ean", clean_text, "Código de barras"),
    "regional": ("regional", clean_text, "Regional"),
    "costo anterior con impuesto saludable": (
        "old_cost_with_tax",
        parse_money,
        "Costo anterior con impuesto saludable",
    ),
    "costo anterior sin impuesto saludable": (
        "old_cost",
        parse_money,
        "Costo anterior sin impuesto saludable",
    ),
    "costo nuevo sin impuesto saludable": (
        "new_cost",
        parse_money,
        "Costo nuevo sin impuesto saludable",
    ),
    "costo nuevo con impuesto saludable": (
        "new_cost_with_tax",
        parse_money,
        "Costo nuevo con impuesto saludable",
    ),
    "estado incremento": ("status", parse_status, "Estado incremento"),
    "observaciones": ("observations", clean_text, "Observaciones"),
    "margen": ("margin_pct", parse_ratio, "Margen"),
    "precio": ("price", parse_money, "Precio"),
    "notificacion": ("notified", parse_flag, "Notificación"),
    "cambio en siesa": ("siesa_changed", parse_flag, "Cambio en SIESA"),
}
_SKU = {"referencia", "referencia jdh", "sku"}
_NEW_COST = {"new_cost", "new_cost_with_tax"}


def _column_reader(raw: Any) -> tuple[str, Callable[[Any], Any], str] | None:
    name = normalize(raw)
    if name in _READERS:
        return _READERS[name]
    text = str(raw or "")
    # "variacion $" and "Variacion %" normalize alike: the symbol tells them apart.
    if name.startswith("variacion"):
        if "%" in text:
            return ("variation_pct", parse_ratio, "Variación %")
        return ("variation_amount", parse_money, "Variación $")
    if name.startswith("icui"):
        return ("icui_pct", parse_ratio, "ICUI")
    if name.startswith("ibua"):
        return ("ibua", parse_money, "IBUA")
    return None


def _open(content: bytes):
    try:
        return openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, OSError, KeyError, ValueError) as exc:
        raise InvalidInputError("El archivo no es un libro de Excel válido") from exc


class PriceIncreaseFileReader:
    """Turns the workbook into rows of increase values, with what each row could not read."""

    def read(self, filename: str | None, content: bytes) -> list[FileRow]:
        """Every row with a reference, in file order.

        Raises:
            InvalidInputError: Not an ``.xlsx``, no header with ``Referencia`` and a new cost,
                or more rows than :data:`MAX_ROWS`.
        """

        if not (filename or "").lower().endswith((".xlsx", ".xlsm")):
            raise InvalidInputError("Sube el acumulado de alzas en Excel (.xlsx)")
        book = _open(content)
        try:
            rows = book.worksheets[0].iter_rows(values_only=True)
            header = None
            for index, row in enumerate(rows, start=1):
                columns = {
                    column: reader
                    for column, cell in enumerate(row)
                    if cell is not None and (reader := _column_reader(cell)) is not None
                }
                sku = next((c for c, cell in enumerate(row) if normalize(cell) in _SKU), None)
                if sku is not None and {item[0] for item in columns.values()} & _NEW_COST:
                    header = (index, sku, columns)
                    break
                if index >= HEADER_SCAN_ROWS:
                    break
            if header is None:
                raise InvalidInputError(
                    "No se encontró el encabezado: se necesita Referencia y Costo Nuevo "
                    "(con o sin impuesto saludable)"
                )
            start, sku_column, columns = header
            result: list[FileRow] = []
            for number, row in enumerate(rows, start=start + 1):
                sku = clean_text(row[sku_column]) if sku_column < len(row) else None
                if sku is None:
                    continue
                if len(result) >= MAX_ROWS:
                    raise InvalidInputError(f"El archivo admite máximo {MAX_ROWS} filas")
                result.append(self._row(number, sku, row, columns))
            return result
        finally:
            book.close()

    @staticmethod
    def _row(number: int, sku: str, row: tuple, columns: dict) -> FileRow:
        values: dict[str, Any] = {"sku": sku}
        problems: list[str] = []
        notes: list[str] = []
        for column, (name, reader, label) in columns.items():
            cell = row[column] if column < len(row) else None
            try:
                values[name] = reader(cell)
            except CellNote as exc:
                # "DESCODIFICADO" where the new cost goes says something about the row.
                notes.append(f"{label}: {exc}")
            except CellError as exc:
                problems.append(f"{label}: «{exc}» no se pudo leer")
        if notes:
            values["observations"] = "; ".join(filter(None, [values.get("observations"), *notes]))
        parsed = PriceIncreaseValues(**values)
        old = parsed.old_cost_with_tax if parsed.old_cost_with_tax is not None else parsed.old_cost
        new = parsed.effective_new_cost
        status = parsed.status or status_of(old, new)
        variation = parsed.variation_amount
        if variation is None and old is not None and new is not None:
            variation = new - old
        values.update(status=status, variation_amount=variation)
        return FileRow(number, PriceIncreaseValues(**values), tuple(problems))


# The area's workbook as it keeps it (``Acumulado_Alza_Precios.xlsx``, «Hoja 1»).
HISTORY_HEADERS = (
    "Fecha de notificacion",
    "Fecha de actualizacion Sistema",
    "Fecha de Incremento",
    "SKU Proveedor",
    "Codigo de Barras EAN 13",
    "Nit",
    "Referencia",
    "Proveedor",
    "Desc. item",
    "Unidad de Empaque",
    "Estado el Item",
    "Categoria",
    "Grupo Impositivo",
    "Descuento Regular",
    "Regional",
    "ICUI (Indique el porcentaje)",
    "IBUA (Indique el valor por unidad)",
    "Costo anterior con Impuesto Saludable",
    "Costo anterior sin Impuesto Saludable",
    "Costo Nuevo sin Impuesto Saludable",
    "Costo nuevo con Impuesto Saludable",
    "variacion $",
    "Variacion %",
    "Estado Incremento",
    "OBSERVACIONES",
    "Margen",
    "Precio",
    "Notificacion",
    "Cambio en siesa",
)
_DATE = "dd/mm/yyyy"
_MONEY = "#,##0.00"
_RATIO = "0.00%"
_TEXT = "@"
_WIDTHS = {"A": 14, "B": 14, "C": 14, "D": 14, "E": 16, "G": 11, "H": 32, "I": 36, "Y": 30}


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def history_workbook(rows: Sequence[ExportRow]) -> bytes:
    """The history as the area's workbook, so it can be kept in the sheet and uploaded back:
    the same 29 columns, a row per increase, the rows of a key in their workbook order.

    Written in streaming mode (50 000 rows in a few seconds and little memory).
    """

    book = openpyxl.Workbook(write_only=True)
    sheet = book.create_sheet("Hoja 1")
    for column, width in _WIDTHS.items():
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = "A2"

    def cell(value: Any, number_format: str | None = None) -> WriteOnlyCell:
        item = WriteOnlyCell(sheet, value=value)
        if number_format:
            item.number_format = number_format
        return item

    header_font = Font(bold=True)
    header = []
    for name in HISTORY_HEADERS:
        item = WriteOnlyCell(sheet, value=name)
        item.font = header_font
        header.append(item)
    sheet.append(header)
    for row in rows:
        values = row.values
        supplier = " - ".join(part for part in (row.supplier_code, row.supplier_name) if part)
        sheet.append(
            [
                cell(values.notified_on, _DATE),
                cell(values.system_updated_on, _DATE),
                cell(values.increased_on, _DATE),
                cell(values.supplier_sku, _TEXT),
                cell(values.ean, _TEXT),
                cell(row.nit, _TEXT),
                cell(values.sku, _TEXT),
                cell(supplier or None),
                cell(row.description),
                cell(_number(row.pack_factor)),
                cell(row.state),
                cell(row.category),
                cell(None),
                cell(None),
                cell(values.regional),
                cell(_number(values.icui_pct), _RATIO),
                cell(_number(values.ibua), _MONEY),
                cell(_number(values.old_cost_with_tax), _MONEY),
                cell(_number(values.old_cost), _MONEY),
                cell(_number(values.new_cost), _MONEY),
                cell(_number(values.new_cost_with_tax), _MONEY),
                cell(_number(values.variation_amount), _MONEY),
                cell(_number(values.variation_pct), _RATIO),
                cell(STATUS_WORDS.get(values.status or "")),
                cell(values.observations),
                cell(_number(values.margin_pct), _RATIO),
                cell(_number(values.price), _MONEY),
                cell("OK" if values.notified else None),
                cell("OK" if values.siesa_changed else None),
            ]
        )
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


__all__ = ["HISTORY_HEADERS", "MAX_ROWS", "PriceIncreaseFileReader", "history_workbook"]
