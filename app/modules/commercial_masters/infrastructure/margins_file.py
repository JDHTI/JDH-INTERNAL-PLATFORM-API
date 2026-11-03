"""Reads and writes the margin file: one row per product, one column per channel margin.

The template is the margin master's own layout (``Maestro Margen.xlsx``), so the same file
that holds the current margins can be edited and uploaded back:

- ``Margen Producto``: ``Referencia`` plus its margin columns and ``IMPTOLIC`` (the national
  liquor tax). Columns are found by header; the ones that are not margins are ignored, so
  the whole master sheet can be uploaded as it is.
- ``Margen Proveedor-Impuestos``: the ``IMPUESTOS`` table (``Sku``, ``Nacional``, ``Costa``,
  ``Grupo impositivo``), the liquor tax by regional. It wins over ``IMPTOLIC``.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

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
TAX_SHEET = "Margen Proveedor-Impuestos"

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
# Liquor tax columns a margin sheet may carry next to the margins.
_ROW_TAX_HEADERS: dict[str, str] = {
    "imptolic": "liquor_tax",
    "impuesto licor nacional": "liquor_tax",
    "impuesto licor costa": "liquor_tax_coast",
    "grupo impositivo": "liquor_tax_group",
}
# Header of the IMPUESTOS table: liquor tax by regional.
_TABLE_HEADERS: dict[str, str] = {
    "sku": "sku",
    "referencia": "sku",
    "nacional": "liquor_tax",
    "costa": "liquor_tax_coast",
    "grupo impositivo": "liquor_tax_group",
}

TEMPLATE_HEADERS = (
    "Referencia",
    "Desc. item",
    "Estado",
    "IMPTOLIC",
    *MARGIN_LABELS.values(),
    "Observaciones",
)
TAX_TABLE_HEADERS = ("Sku", "Descripción", "Nacional", "Costa", "Grupo impositivo", "Observación")
# The master puts the IMPUESTOS table in columns I:N, after the supplier margins (A:G).
_TAX_TABLE_OFFSET = 8


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = " ".join(str(value).split())
    return text or None


def _cell(row: Sequence[Any], column: int | None) -> Any:
    return row[column] if column is not None and column < len(row) else None


def parse_tax(value: Any) -> tuple[bool, Decimal | None]:
    """Read a liquor tax cell: blank leaves it, ``0`` clears it, else pesos per unit.

    Raises:
        ValueError: If the cell is not a number or is negative.
    """

    if value is None or (isinstance(value, str) and not value.strip()):
        return False, None
    if isinstance(value, bool):
        raise ValueError(value)
    text = str(value).strip().replace("$", "").replace(" ", "")
    if isinstance(value, str) and "," in text:
        # "6.911,00" or "6911,5": Colombian thousands and decimals.
        text = text.replace(".", "").replace(",", ".")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(value) from exc
    if not number.is_finite() or number < 0:
        raise ValueError(value)
    return True, (number if number > 0 else None)


def _header_map(rows: Sequence[Sequence[Any]]) -> tuple[int, dict[str, int]] | None:
    """The first row with a SKU column and at least one margin column."""

    for index, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        names = [normalize(cell) for cell in row]
        if "nacional" in names:
            # The IMPUESTOS table shares its row with the supplier margins: not a product sheet.
            continue
        positions: dict[str, int] = {}
        for column, name in enumerate(names):
            if name in SKU_HEADERS:
                positions.setdefault("sku", column)
            elif name in _MARGIN_HEADERS:
                positions.setdefault(_MARGIN_HEADERS[name], column)
            elif name in NOTES_HEADERS:
                positions.setdefault("notes", column)
            elif name in _ROW_TAX_HEADERS:
                positions.setdefault(_ROW_TAX_HEADERS[name], column)
        if "sku" in positions and any(field in positions for field in MARGIN_FIELDS):
            return index, positions
    return None


def _tax_problem(column: str, cell: Any) -> str:
    labels = {
        "liquor_tax": "Impuesto licor Nacional",
        "liquor_tax_coast": "Impuesto licor Costa",
    }
    return f"{labels[column]} inválido: {cell!r}"


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
        sku = _text(_cell(row, positions["sku"]))
        if sku is None:
            continue
        values: dict[str, Decimal | None] = {}
        problems: list[str] = []
        for field in MARGIN_FIELDS:
            cell = _cell(row, positions.get(field))
            try:
                present, value = parse_margin(cell)
            except ValueError:
                problems.append(f"{MARGIN_LABELS[field]} inválido: {cell!r}")
                continue
            if present:
                values[field] = value
        taxes: dict[str, Decimal | str | None] = {}
        for field in ("liquor_tax", "liquor_tax_coast"):
            if field not in positions:
                continue
            cell = _cell(row, positions[field])
            try:
                present, tax = parse_tax(cell)
            except ValueError:
                problems.append(_tax_problem(field, cell))
                continue
            if present:
                taxes[field] = tax
        if "liquor_tax_group" in positions:
            group = _text(_cell(row, positions["liquor_tax_group"]))
            if group is not None:
                taxes["liquor_tax_group"] = None if group == "0" else group
        if "liquor_tax" in taxes and taxes["liquor_tax"] is None:
            # IMPTOLIC 0: the product pays no liquor tax in any regional.
            taxes.setdefault("liquor_tax_coast", None)
            taxes.setdefault("liquor_tax_group", None)
        notes = _text(_cell(row, positions.get("notes")))
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
                taxes=taxes,
            )
        )
    return result


def _table_header(rows: Sequence[Sequence[Any]]) -> tuple[int, dict[str, int]] | None:
    """First data row and columns of the ``IMPUESTOS`` table (``Sku`` and ``Nacional``)."""

    for index, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        names = {normalize(cell): column for column, cell in enumerate(row) if cell is not None}
        if "nacional" in names and ("sku" in names or "referencia" in names):
            columns = {
                field: names[name] for name, field in _TABLE_HEADERS.items() if name in names
            }
            return index + 1, columns
    return None


def tax_table(rows: Iterable[Sequence[Any]]) -> dict[str, dict[str, Decimal | str | None]]:
    """The ``IMPUESTOS`` table of a sheet: liquor tax by regional per SKU.

    Returns an empty dict when the sheet has no such table (a header with ``Sku`` and
    ``Nacional``). A row with an unreadable value is skipped.
    """

    materialized = [list(row) for row in rows]
    found = _table_header(materialized)
    if found is None:
        return {}
    start, columns = found
    table: dict[str, dict[str, Decimal | str | None]] = {}
    for row in materialized[start:]:
        sku = _text(_cell(row, columns["sku"]))
        if sku is None:
            continue
        try:
            _, national = parse_tax(_cell(row, columns.get("liquor_tax")))
            _, coast = parse_tax(_cell(row, columns.get("liquor_tax_coast")))
        except ValueError:
            continue
        group = _text(_cell(row, columns.get("liquor_tax_group")))
        if national is None and coast is None:
            continue
        table[sku] = {
            "liquor_tax": national if national is not None else coast,
            # Same value on the Coast when the table leaves Costa empty.
            "liquor_tax_coast": coast if coast is not None else national,
            "liquor_tax_group": group,
        }
    return table


def merge_taxes(
    rows: Sequence[MarginRow], table: dict[str, dict[str, Decimal | str | None]]
) -> list[MarginRow]:
    """Put the ``IMPUESTOS`` table on the rows: it wins over ``IMPTOLIC``.

    A SKU of the table that is not in the margin sheet becomes a row of its own.
    """

    if not table:
        return list(rows)
    seen: set[str] = set()
    merged: list[MarginRow] = []
    for row in rows:
        seen.add(row.sku)
        taxes = table.get(row.sku)
        merged.append(replace(row, taxes={**row.taxes, **taxes}) if taxes else row)
    last = max((row.row_number for row in rows), default=1)
    for offset, (sku, taxes) in enumerate(table.items(), start=1):
        if sku not in seen:
            merged.append(MarginRow(row_number=last + offset, sku=sku, taxes=dict(taxes)))
    return merged


def _load(content: bytes):
    try:
        return openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, OSError, KeyError, ValueError) as exc:
        raise InvalidInputError("El archivo no es un libro de Excel válido") from exc


def _xlsx_rows(content: bytes, sheet: str | None) -> tuple[list[list[Any]], dict[str, Any]]:
    """The margin sheet's cells and the liquor tax table of the workbook (if any)."""

    book = _load(content)
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
        chosen: list[list[Any]] | None = None
        for item in sheets:
            rows = [list(row) for row in item.iter_rows(values_only=True)]
            if _header_map(rows) is not None:
                chosen = rows
                break
        if chosen is None:
            chosen = [list(row) for row in sheets[0].iter_rows(values_only=True)]
        taxes: dict[str, Any] = {}
        for item in book.worksheets:
            # The first rows decide whether a sheet holds the table; only then read it whole.
            head = [list(row) for row in item.iter_rows(max_row=HEADER_SCAN_ROWS, values_only=True)]
            if _table_header(head) is not None:
                taxes = tax_table(item.iter_rows(values_only=True))
                break
        return chosen, taxes
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
    """Reads margin rows (and the liquor tax table) from Excel (``.xlsx``/``.xlsm``) or CSV."""

    def read(self, filename: str, content: bytes, sheet: str | None = None) -> list[MarginRow]:
        """Margin rows of the uploaded file, with the liquor taxes by regional.

        Raises:
            InvalidInputError: If the format is not supported or the header is missing.
        """

        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if extension not in EXTENSIONS:
            raise InvalidInputError(
                f"Formato no soportado: .{extension or '?'} (usa {', '.join(EXTENSIONS)})"
            )
        if extension == "csv":
            return rows_from_sheet(_csv_rows(content))
        rows, taxes = _xlsx_rows(content, sheet)
        return merge_taxes(rows_from_sheet(rows), taxes)


def _number(value: Decimal | None) -> float | int | None:
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def template_workbook(products: Sequence[Product]) -> bytes:
    """The margin template as an ``.xlsx``, in the margin master's layout.

    With ``products`` it carries the current margins and, in ``Margen Proveedor-Impuestos``,
    the liquor tax by regional of the taxed products.
    """

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
                _number(product.liquor_tax) or 0,
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
    taxes = book.create_sheet(TAX_SHEET)
    pad = [None] * _TAX_TABLE_OFFSET
    taxes.append([*pad, "IMPUESTOS"])
    taxes.append([*pad, *TAX_TABLE_HEADERS])
    for product in products:
        if product.liquor_tax is None and product.liquor_tax_coast is None:
            continue
        taxes.append(
            [
                *pad,
                product.sku,
                product.description,
                _number(product.liquor_tax),
                _number(product.liquor_tax_coast or product.liquor_tax),
                product.liquor_tax_group,
                None,
            ]
        )
    help_sheet = book.create_sheet("Instrucciones")
    for line in (
        "Una fila por producto. Solo se usan Referencia, IMPTOLIC, las columnas de margen y "
        "Observaciones.",
        "Referencia es la referencia JDH (SKU) del maestro de productos.",
        "Un margen se escribe como fracción (0.18) o en puntos (18 o 18 %).",
        "Una celda vacía deja el margen como está.",
        "Un 0 significa «sin margen definido» y lo borra.",
        "Una referencia que no existe en el maestro se reporta y no se aplica.",
        f"Impuesto al licor: tabla IMPUESTOS de la hoja «{TAX_SHEET}» (Sku, Nacional, Costa, "
        "Grupo impositivo), en pesos por unidad. Costa aplica a la regional de facturación "
        "004; las demás regionales usan Nacional. La tabla manda sobre IMPTOLIC.",
        "IMPTOLIC en 0 quita el impuesto al licor del producto.",
    ):
        help_sheet.append([line])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


__all__ = [
    "SpreadsheetMarginReader",
    "merge_taxes",
    "parse_tax",
    "rows_from_sheet",
    "tax_table",
    "template_workbook",
]
