"""Reads and writes the margin file: one row per product, one column per channel margin.

The template is the margin master's own layout (``Referencia`` plus its margin columns), so
the same file that holds the current margins can be edited and uploaded back. Columns are
found by header; the ones that are not margins are ignored, so the whole master sheet can be
uploaded as it is.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any

from app.core.errors import InvalidInputError
from app.modules.commercial_masters.domain.entities import Product
from app.modules.commercial_masters.domain.margins import (
    MARGIN_FIELDS,
    MARGIN_LABELS,
    MarginRow,
    normalize,
    parse_margin,
)

SKU_HEADERS = {"referencia", "referencia jdh", "sku"}
NOTES_HEADERS = {"observaciones", "observacion", "notas"}
EXTENSIONS = ("xlsx", "xlsm", "csv")
HEADER_SCAN_ROWS = 10
# The master sheet is "Margen Producto "; the older "Margen Productos" has other columns.
PREFERRED_SHEET = "margen producto"

_MARGIN_HEADERS: dict[str, str] = {
    "margen lista 1": "margin_list",
    "margen lista": "margin_list",
    "margen food service": "margin_list",
    "margen mayorista": "margin_wholesale",
    "margen mayoristas": "margin_wholesale",
    "margen masivo food": "margin_mass_food",
    "margen market apartado": "margin_market",
    "margen salsamentarias": "margin_salsamentarias",
}

TEMPLATE_HEADERS = (
    "Referencia",
    "Desc. item",
    "Estado",
    *MARGIN_LABELS.values(),
    "Observaciones",
)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = " ".join(str(value).split())
    return text or None


def _header_map(rows: Sequence[Sequence[Any]]) -> tuple[int, dict[str, int]] | None:
    """The first row with a SKU column and at least one margin column."""

    for index, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        positions: dict[str, int] = {}
        for column, cell in enumerate(row):
            name = normalize(cell)
            if name in SKU_HEADERS:
                positions.setdefault("sku", column)
            elif name in _MARGIN_HEADERS:
                positions.setdefault(_MARGIN_HEADERS[name], column)
            elif name in NOTES_HEADERS:
                positions.setdefault("notes", column)
        if "sku" in positions and any(field in positions for field in MARGIN_FIELDS):
            return index, positions
    return None


def rows_from_sheet(rows: Iterable[Sequence[Any]]) -> list[MarginRow]:
    """Margin rows from the cells of a sheet (header included).

    Raises:
        InvalidInputError: If there is no header with a SKU column and a margin column.
    """

    materialized = [list(row) for row in rows]
    found = _header_map(materialized)
    if found is None:
        raise InvalidInputError(
            "No se encontró el encabezado: se necesita la columna Referencia y al menos una "
            f"columna de margen ({', '.join(MARGIN_LABELS.values())})"
        )
    header_index, positions = found
    result: list[MarginRow] = []
    for offset, row in enumerate(materialized[header_index + 1 :], start=header_index + 2):
        sku = _text(row[positions["sku"]] if positions["sku"] < len(row) else None)
        if sku is None:
            continue
        values: dict[str, Decimal | None] = {}
        problems: list[str] = []
        for field in MARGIN_FIELDS:
            column = positions.get(field)
            cell = row[column] if column is not None and column < len(row) else None
            try:
                present, value = parse_margin(cell)
            except ValueError:
                problems.append(f"{MARGIN_LABELS[field]} inválido: {cell!r}")
                continue
            if present:
                values[field] = value
        notes = _text(row[positions["notes"]]) if positions.get("notes", 10**9) < len(row) else None
        # The master writes 0 where it has no note.
        has_notes = notes is not None and notes != "0"
        result.append(
            MarginRow(
                row_number=offset,
                sku=sku,
                values=values,
                notes=notes if has_notes else None,
                has_notes=has_notes,
                problems=tuple(problems),
            )
        )
    return result


def _xlsx_rows(content: bytes, sheet: str | None) -> list[list[Any]]:
    import openpyxl
    from openpyxl.utils.exceptions import InvalidFileException

    try:
        book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, OSError, KeyError, ValueError) as exc:
        raise InvalidInputError("El archivo no es un libro de Excel válido") from exc
    try:
        sheets = book.worksheets
        if sheet:
            wanted = normalize(sheet)
            sheets = [item for item in sheets if normalize(item.title) == wanted]
            if not sheets:
                raise InvalidInputError(f"El libro no tiene la hoja «{sheet}»")
        else:
            # Prefer the margin master's own sheet over older working copies.
            sheets = sorted(sheets, key=lambda item: normalize(item.title) != PREFERRED_SHEET)
        for item in sheets:
            rows = [list(row) for row in item.iter_rows(values_only=True)]
            if _header_map(rows) is not None:
                return rows
        return [list(row) for row in sheets[0].iter_rows(values_only=True)]
    finally:
        book.close()


def _csv_rows(content: bytes) -> list[list[str]]:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - cp1252 decodes almost anything
        raise InvalidInputError("No se pudo leer el texto del CSV")
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(
            text[:4096], delimiters=",;\t"
        )
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect)]


class SpreadsheetMarginReader:
    """Reads margin rows from Excel (``.xlsx``/``.xlsm``) or CSV."""

    def read(self, filename: str, content: bytes, sheet: str | None = None) -> list[MarginRow]:
        """Margin rows of the uploaded file.

        Raises:
            InvalidInputError: If the format is not supported or the header is missing.
        """

        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if extension not in EXTENSIONS:
            raise InvalidInputError(
                f"Formato no soportado: .{extension or '?'} (usa {', '.join(EXTENSIONS)})"
            )
        rows = _csv_rows(content) if extension == "csv" else _xlsx_rows(content, sheet)
        return rows_from_sheet(rows)


def template_workbook(products: Sequence[Product]) -> bytes:
    """The margin template as an ``.xlsx``; with ``products`` it carries the current margins."""

    import openpyxl

    book = openpyxl.Workbook(write_only=True)
    sheet = book.create_sheet("Margen Producto")
    sheet.append(list(TEMPLATE_HEADERS))
    for product in products:
        margins = product.margins
        sheet.append(
            [
                product.sku,
                product.description,
                product.state,
                *(
                    None if value is None else float(value)
                    for value in (
                        margins.list,
                        margins.wholesale,
                        margins.mass_food,
                        margins.market,
                        margins.salsamentarias,
                    )
                ),
                margins.notes,
            ]
        )
    help_sheet = book.create_sheet("Instrucciones")
    for line in (
        "Una fila por producto. Solo se usan Referencia, las columnas de margen y Observaciones.",
        "Referencia es la referencia JDH (SKU) del maestro de productos.",
        "Un margen se escribe como fracción (0.18) o en puntos (18 o 18 %).",
        "Una celda vacía deja el margen como está.",
        "Un 0 significa «sin margen definido» y lo borra.",
        "Una referencia que no existe en el maestro se reporta y no se aplica.",
    ):
        help_sheet.append([line])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


__all__ = ["SpreadsheetMarginReader", "rows_from_sheet", "template_workbook"]
