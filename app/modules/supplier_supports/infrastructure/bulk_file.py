"""Reads the file of a bulk load of supports and writes its template.

The reader only returns cells; which sheet and row hold the header, and what each column
means, is decided by the application because it depends on the supplier's profile.
"""

from __future__ import annotations

import csv
import io
import zipfile
from typing import Any

import openpyxl
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException

from app.core.errors import InvalidInputError
from app.modules.supplier_supports.application import normalize as n
from app.modules.supplier_supports.domain import fields, vocabulary
from app.modules.supplier_supports.domain.profiles import Profile

EXTENSIONS = ("xlsx", "xlsm", "csv")
TEMPLATE_SHEET = "Apoyos"
_HEADER_FILL = PatternFill("solid", start_color="1F3A5F")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_REQUIRED_FILL = PatternFill("solid", start_color="B3261E")

Sheet = tuple[str, list[list[Any]]]


def _csv_rows(content: bytes) -> list[list[Any]]:
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
    return [list(row) for row in csv.reader(io.StringIO(text), dialect)]


def _xlsx_sheets(content: bytes, sheet: str | None) -> list[Sheet]:
    try:
        book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, OSError, KeyError, ValueError) as exc:
        raise InvalidInputError("El archivo no es un libro de Excel válido") from exc
    try:
        worksheets = book.worksheets
        if sheet:
            wanted = n.fold(sheet)
            worksheets = [item for item in worksheets if n.fold(item.title) == wanted]
            if not worksheets:
                raise InvalidInputError(f"El libro no tiene la hoja «{sheet}»")
        else:
            # The template's own sheet first, then the others in book order.
            worksheets = sorted(worksheets, key=lambda item: item.title != TEMPLATE_SHEET)
        return [
            (item.title, [list(row) for row in item.iter_rows(values_only=True)])
            for item in worksheets
        ]
    finally:
        book.close()


class SpreadsheetSupportReader:
    """Reads Excel (``.xlsx``/``.xlsm``) and CSV files as lists of rows."""

    def read(self, filename: str, content: bytes, sheet: str | None = None) -> list[Sheet]:
        """The sheets of the file, each as a list of rows of cells.

        Raises:
            InvalidInputError: If the format is not supported or the file cannot be read.
        """

        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if extension not in EXTENSIONS:
            raise InvalidInputError(
                f"Formato no soportado: .{extension or '?'} (usa {', '.join(EXTENSIONS)})"
            )
        if extension == "csv":
            return [(filename, _csv_rows(content))]
        return _xlsx_sheets(content, sheet)


def template_headers(profile: Profile | None) -> list[tuple[str, str]]:
    """``(field, header)`` of every column of the template.

    With a profile the columns follow the supplier's own layout: fields it fixes with a
    default are left out, and the first header it maps to a field replaces the general one.
    """

    columns: list[tuple[str, str]] = []
    for spec in fields.FIELD_SPECS:
        if not spec.in_template:
            continue
        if profile is not None and spec.name in profile.data.defaults:
            continue
        header = spec.label
        if profile is not None and profile.data.column_map.get(spec.name):
            header = profile.data.column_map[spec.name][0]
        columns.append((spec.name, header))
    return columns


def template_workbook(profile: Profile | None = None) -> bytes:
    """The bulk template as an ``.xlsx``: the sheet to fill, the instructions and the values."""

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = TEMPLATE_SHEET
    columns = template_headers(profile)
    defaults = profile.data.defaults if profile else {}
    required = {"kind", "calc_method", "supplier_code"} - set(defaults)
    if profile is not None:
        required.discard("supplier_code")
    for index, (name, header) in enumerate(columns, start=1):
        cell = sheet.cell(row=1, column=index, value=header)
        cell.font = _HEADER_FONT
        cell.fill = _REQUIRED_FILL if name in required else _HEADER_FILL
        sheet.column_dimensions[get_column_letter(index)].width = max(14, len(header) + 4)
    sheet.freeze_panes = "A2"

    guide = book.create_sheet("Instrucciones")
    guide.append(["Carga masiva de apoyos"])
    guide["A1"].font = Font(bold=True, size=14)
    intro = [
        "Una fila por apoyo. Las columnas en rojo son obligatorias; el resto es opcional.",
        "Antes de guardar se simula: si una sola fila tiene un error no se guarda ninguna, "
        "y el reporte dice qué corregir y en qué fila.",
        "Los porcentajes pueden escribirse como 5 %, 5 o 0,05. Los campos de alcance vacíos "
        "significan «todos».",
        "Fechas: dd/mm/aaaa o aaaa-mm-dd. Fecha final vacía, INDEFINIDO o UFN = sin fecha final.",
        "Referencia JDH, NIT y proveedor deben existir en los maestros; si no, la fila falla.",
    ]
    if profile is not None:
        intro.insert(
            0,
            f"Perfil de {profile.supplier_name} ({profile.supplier_code}): "
            "las columnas con valor fijo ya no aparecen.",
        )
    for line in intro:
        guide.append([line])
    guide.append([])
    guide.append(["Campo", "Encabezado", "Obligatorio", "Descripción", "Valores válidos"])
    for cell in guide[guide.max_row]:
        cell.font = Font(bold=True)
    headers = dict(columns)
    for spec in fields.FIELD_SPECS:
        if spec.name not in headers:
            continue
        guide.append(
            [
                spec.name,
                headers[spec.name],
                "Sí" if spec.name in required else "",
                spec.help,
                ", ".join(spec.choices),
            ]
        )
    for letter, width in zip("ABCDE", (22, 26, 12, 70, 60), strict=True):
        guide.column_dimensions[letter].width = width

    values = book.create_sheet("Valores")
    lists = (
        ("Tipo", vocabulary.KINDS),
        ("Método de cálculo", vocabulary.CALC_METHODS),
        ("Quién financia", vocabulary.FUNDINGS),
        ("Unidad del costo", vocabulary.VALUE_UNITS),
        ("Forma de liquidación", vocabulary.SETTLEMENTS),
    )
    for column, (title, items) in enumerate(lists, start=1):
        values.cell(row=1, column=column, value=title).font = Font(bold=True)
        values.column_dimensions[get_column_letter(column)].width = 26
        for row, item in enumerate(items, start=2):
            values.cell(row=row, column=column, value=item)

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


__all__ = ["SpreadsheetSupportReader", "template_headers", "template_workbook"]
