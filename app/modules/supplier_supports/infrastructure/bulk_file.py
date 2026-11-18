"""Reads the file of a bulk load of supports and writes its template.

The reader only returns cells; which sheet and row hold the header, and what each column
means, is decided by the application because it depends on the supplier's profile.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any
from xml.etree import ElementTree

import openpyxl
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.datavalidation import DataValidation

from app.core.errors import InvalidInputError
from app.modules.supplier_supports.application import normalize as n
from app.modules.supplier_supports.domain import fields, vocabulary
from app.modules.supplier_supports.domain.profiles import Profile

EXTENSIONS = ("xlsx", "xlsm", "csv")
TEMPLATE_SHEET = "Apoyos"
# Header colors, white bold text on all three (contrast 6.6, 5.0 and 11.8 to 1):
# red = required in every row, orange = required for some types, blue = optional.
_REQUIRED_COLOR, _BY_TYPE_COLOR, _OPTIONAL_COLOR = "B3261E", "B45309", "1F3A5F"
_HEADER_FONT = Font(bold=True, color="FFFFFF")


def _fill(color: str) -> PatternFill:
    return PatternFill("solid", start_color=color, end_color=color)


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


_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _resolve(base: str, target: str) -> str:
    """A relationship target as a path inside the package."""

    if target.startswith("/"):
        return target.lstrip("/")
    parts = base.rsplit("/", 1)[0].split("/") if "/" in base else []
    for piece in target.split("/"):
        if piece == "..":
            parts = parts[:-1]
        elif piece and piece != ".":
            parts.append(piece)
    return "/".join(parts)


def _relations(package: zipfile.ZipFile, part: str) -> dict[str, str]:
    folder, name = part.rsplit("/", 1)
    rels = f"{folder}/_rels/{name}.rels"
    if rels not in package.namelist():
        return {}
    root = ElementTree.fromstring(package.read(rels))
    return {
        item.get("Id", ""): _resolve(part, item.get("Target", ""))
        for item in root.findall("rel:Relationship", _NS)
    }


def _tables(content: bytes) -> dict[str, list[tuple[str, str]]]:
    """Sheet title -> ``(table name, A1 range)`` of its Excel tables (ListObjects).

    Read from the package XML: ``read_only`` workbooks do not expose them, and loading the
    whole workbook again just for this would double the time of a big file.
    """

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as package:
            workbook = ElementTree.fromstring(package.read("xl/workbook.xml"))
            targets = _relations(package, "xl/workbook.xml")
            found: dict[str, list[tuple[str, str]]] = {}
            for item in workbook.findall("main:sheets/main:sheet", _NS):
                part = targets.get(item.get(_R_ID, ""))
                if not part:
                    continue
                sheet_xml = ElementTree.fromstring(package.read(part))
                rels = _relations(package, part)
                for table_part in sheet_xml.findall("main:tableParts/main:tablePart", _NS):
                    table_path = rels.get(table_part.get(_R_ID, ""))
                    if not table_path or table_path not in package.namelist():
                        continue
                    table = ElementTree.fromstring(package.read(table_path))
                    name = table.get("displayName") or table.get("name") or ""
                    if name and table.get("ref"):
                        found.setdefault(item.get("name", ""), []).append((name, table.get("ref")))
            return found
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
        return {}


def _slice(rows: list[list[Any]], ref: str) -> list[list[Any]]:
    """The cells of an A1 range (an Excel table) out of a sheet's rows."""

    min_col, min_row, max_col, max_row = range_boundaries(ref)
    return [
        list(row[min_col - 1 : max_col]) + [None] * max(0, max_col - len(row))
        for row in rows[min_row - 1 : max_row]
    ]


def _xlsx_sheets(content: bytes, sheet: str | None) -> list[Sheet]:
    """Every Excel table of the book (``sheet / table``) and then every whole sheet.

    Suppliers keep their supports in a table with a lookup table beside it on the same sheet
    (``Maestro_Sku_*``); reading the table alone keeps those rows out. ``sheet`` names a sheet
    or a table.
    """

    try:
        book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, OSError, KeyError, ValueError) as exc:
        raise InvalidInputError("El archivo no es un libro de Excel válido") from exc
    tables = _tables(content)
    try:
        candidates: list[Sheet] = []
        whole: list[Sheet] = []
        # The template's own sheet first, then the others in book order.
        for item in sorted(book.worksheets, key=lambda value: value.title != TEMPLATE_SHEET):
            rows = [list(row) for row in item.iter_rows(values_only=True)]
            for name, ref in tables.get(item.title, []):
                candidates.append((f"{item.title} / {name}", _slice(rows, ref)))
            whole.append((item.title, rows))
        found = candidates + whole
        if sheet:
            wanted = n.fold(sheet)
            found = [
                (title, rows)
                for title, rows in found
                if wanted in (n.fold(title), n.fold(title.split(" / ")[-1]))
            ]
            if not found:
                raise InvalidInputError(f"El libro no tiene la hoja o tabla «{sheet}»")
        return found
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


_CUSTOMER_COLUMNS = ("customer_nit", "customer_name", "customer_branch_id")
# The download with data adds these to the short template: the reason of a «Validación» No
# and whether each support applies in the promotional warehouses.
_EXPORT_ONLY = ("notes", "promo_warehouses")
# The «Validación» column the bulk load reads (``application.bulk.VALIDATION``).
VALIDATION = "validation"
VALIDATION_HEADER = "Validación"
_UNIT_LABELS = {"unidad": "Unidad", "caja": "Caja", "kg": "Kg"}
# Rows the dropdowns, date checks and date format cover below the filled ones.
_VALIDATED_ROWS = 2000
_DATE_FORMAT = "dd/mm/yyyy"


def template_headers(
    profile: Profile | None, full: bool = False, groups: bool = False, validation: bool = False
) -> list[tuple[str, str]]:
    """``(field, header)`` of every column of the template.

    By default the short general template, in BI's order and with nothing else (2026-11-18):
    Clase apoyo, Tipo apoyo, Valor apoyo, Unidad valor, Excepción margen mínimo autorizado
    JDH, Fecha Inicio, Fecha Fin, NIT Cliente, Cliente, Sucursal, Grupo Empresarial, SKU JDH,
    SKU Proveedor, Regional, Unidad Negocio, Canal and Bodega; costs, prices and margins come
    from the DWH and are never asked. ``validation`` (the download with data) adds
    Observaciones, the reason of a «Validación» No. ``full`` adds every field. ``groups`` is
    the business-group upload: Grupo Empresarial without the customer columns. With a profile
    the columns follow the supplier's own layout: fields it fixes with a default are left
    out, the fields it maps are added, and its first header replaces the general one.
    """

    columns: list[tuple[str, str]] = []
    mapped = set(profile.data.column_map) if profile is not None else set()
    for spec in fields.FIELD_SPECS:
        wanted = (
            full or spec.core or spec.name in mapped or (validation and spec.name in _EXPORT_ONLY)
        )
        if groups and spec.name == "customer_group":
            wanted = True
        if groups and spec.name in _CUSTOMER_COLUMNS:
            wanted = False
        if not spec.in_template or not wanted:
            continue
        if profile is not None and spec.name in profile.data.defaults:
            continue
        header = spec.label
        if profile is not None and profile.data.column_map.get(spec.name):
            header = profile.data.column_map[spec.name][0]
        columns.append((spec.name, header))
    return columns


# The bulk load reads these from the one margin column of the short template when the file has
# no column of their own (``application.bulk``: minimum margin and guaranteed margin).
_STANDS_IN = {"min_margin_pct": "supported_margin_pct", "target_margin_pct": "supported_margin_pct"}


def column_requirements(
    columns: Sequence[tuple[str, str]], defaults: Mapping[str, Any], groups: bool = False
) -> dict[str, tuple[str, ...]]:
    """Which columns the bulk load requires, from the rules it validates with.

    ``field -> ()`` when every row needs it (:data:`fields.REQUIRED_ALWAYS`, and Grupo
    Empresarial in the business-group upload); ``field -> calc methods`` when only those types
    need it (:func:`fields.methods_requiring`); optional columns are left out. A calc method
    fixed by the supplier's profile turns its fields into always required.
    """

    names = {name for name, _ in columns}
    always = set(fields.REQUIRED_ALWAYS) | ({"customer_group"} if groups else set())
    fixed = defaults.get("calc_method")
    by_type: dict[str, tuple[str, ...]] = {}
    for name, methods in fields.methods_requiring().items():
        target = name if name in names else _STANDS_IN.get(name)
        if target is None or target not in names:
            continue
        if fixed is not None:
            if fixed in methods:
                always.add(target)
            continue
        by_type[target] = tuple(dict.fromkeys((*by_type.get(target, ()), *methods)))
    return by_type | {name: () for name in always & names}


def _requirement_text(methods: tuple[str, ...] | None) -> str:
    """The «Obligatorio» cell of the instructions for a column."""

    if methods is None:
        return ""
    if not methods:
        return "Sí"
    labels = vocabulary.CALC_METHOD_LABELS
    others = [method for method in fields.active_methods() if method not in methods]
    if len(others) < len(methods):
        return "Sí, salvo con Tipo apoyo " + " o ".join(labels[item] for item in others)
    return "Con Tipo apoyo " + " o ".join(labels[item] for item in methods)


def _shown(name: str, value: Any) -> Any:
    """A stored value as the template writes it (labels, percentages, plain dates)."""

    if value is None or value == ():
        return None
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if name == "kind":
        return vocabulary.KIND_LABELS.get(value, value)
    if name == "calc_method":
        return vocabulary.CALC_METHOD_LABELS.get(value, value)
    if name == "value_unit":
        return _UNIT_LABELS.get(value, value)
    if isinstance(value, tuple | list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def _active_methods() -> list[str]:
    return [vocabulary.CALC_METHOD_LABELS[method] for method in fields.active_methods()]


def template_workbook(
    profile: Profile | None = None,
    full: bool = False,
    *,
    groups: bool = False,
    rows: Sequence[Mapping[str, Any]] = (),
    validation: bool = False,
) -> bytes:
    """The bulk template as an ``.xlsx``: the sheet to fill, the instructions and the values.

    Clase apoyo, Tipo apoyo and Unidad valor are dropdowns, and the dates only take dates.
    ``rows`` pre-fills it (the download with data: ``field -> stored value``) and
    ``validation`` adds the «Validación» column (Sí / No) for the supplier to confirm each
    support; the file goes back through the same bulk load.
    """

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = TEMPLATE_SHEET
    columns = template_headers(profile, full, groups, validation)
    defaults = profile.data.defaults if profile else {}
    required = column_requirements(columns, defaults, groups)
    headers = [header for _, header in columns]
    if validation:
        headers.append(VALIDATION_HEADER)
    for index, header in enumerate(headers, start=1):
        name = columns[index - 1][0] if index <= len(columns) else VALIDATION
        cell = sheet.cell(row=1, column=index, value=header)
        cell.font = _HEADER_FONT
        methods = required.get(name)
        color = (
            _OPTIONAL_COLOR
            if methods is None
            else _REQUIRED_COLOR
            if not methods
            else _BY_TYPE_COLOR
        )
        cell.fill = _fill(color)
        if methods:
            cell.comment = Comment(f"Obligatorio: {_requirement_text(methods)}.", "Plataforma")
        sheet.column_dimensions[get_column_letter(index)].width = max(14, len(header) + 4)
    sheet.freeze_panes = "A2"
    for row_number, record in enumerate(rows, start=2):
        for index, (name, _) in enumerate(columns, start=1):
            value = _shown(name, record.get(name))
            cell = sheet.cell(row=row_number, column=index, value=value)
            if fields.FIELDS[name].type == fields.FRACTION and value is not None:
                cell.number_format = "0.00%"

    guide = book.create_sheet("Instrucciones")
    guide.append(["Carga masiva de apoyos"])
    guide["A1"].font = Font(bold=True, size=14)
    intro = [
        "Una fila por apoyo. El color del encabezado dice si la columna es obligatoria (ver "
        "los colores abajo y la columna Obligatorio de la tabla).",
        "Llave del apoyo: proveedor + NIT cliente + SKU JDH + fecha inicio. Si la llave ya "
        "existe, la fila actualiza ese apoyo; si no, lo crea. Una columna que no venga en el "
        "archivo conserva el valor guardado. La fecha inicio es parte de la llave pero no es "
        "obligatoria: vacía = desde siempre.",
        "Desde el NIT se toman del maestro de clientes el nombre del cliente y su grupo "
        "empresarial; la sucursal debe ser una del NIT (1 y 001 son la misma). El NIT puede "
        "venir con o sin dígito de verificación.",
        "Desde el SKU JDH se toman del maestro de productos el SKU Proveedor, la marca y la "
        "unidad de empaque. Solo si la fila no trae SKU JDH, el SKU Proveedor se usa para "
        "encontrarlo: si no corresponde a ninguno, o a varios, la fila falla.",
        "La columna Excepción margen mínimo autorizado JDH es el margen de la Excepción margen "
        "JDH y el margen garantizado del % Margen Garantizado; en los demás tipos, el margen "
        "mínimo que da el precio apoyado mínimo.",
        "Grupo Empresarial (sin NIT): la fila crea un apoyo por cada cliente activo del grupo.",
        "Solo se escribe lo que se negocia: el costo, el precio lista, el margen y los nombres "
        "salen del DWH y se calculan en la plataforma.",
        "Clase apoyo: "
        + "; ".join(vocabulary.KIND_LABELS[kind] for kind in vocabulary.ACTIVE_KINDS)
        + ". Tipo apoyo: "
        + "; ".join(_active_methods())
        + " (listas desplegables).",
        "Unidad valor: Unidad o Caja. Un valor en pesos por caja se divide por la unidad de "
        "empaque del maestro de productos (vacía = 1).",
        "No se permiten dos apoyos de la misma clase, cliente, sucursal y referencia con "
        "vigencias que se cruzan: al cargar se elige si se vence o se anula el anterior.",
        "Validación (descarga con datos): Sí = el proveedor lo confirma (se actualiza con lo "
        "que traiga la fila), No = se vence ayer con la observación de la fila, vacía = no se "
        "toca.",
        "Antes de guardar se simula: si una sola fila tiene un error no se guarda ninguna, "
        "y el reporte dice qué corregir y en qué fila, y qué apoyos se crean, cambian, "
        "quedan igual o ya no vienen.",
        "Los porcentajes pueden escribirse como 5 %, 5 o 0,05. Los campos de alcance vacíos "
        "significan «todos».",
        "Fechas: las columnas de fecha ya tienen formato dd/mm/aaaa; también se lee "
        "aaaa-mm-dd. Fecha Fin vacía, INDEFINIDO o UFN = sin fecha final.",
        "SKU JDH, NIT y proveedor deben existir en los maestros; si no, la fila falla. El "
        "proveedor es el que se elige al subir (o la columna Cod. Proveedor si el archivo la "
        "trae).",
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
    for color, title, meaning in (
        (_REQUIRED_COLOR, "Rojo", "Obligatoria en toda fila."),
        (
            _BY_TYPE_COLOR,
            "Naranja",
            "Obligatoria según el Tipo apoyo: la columna Obligatorio de la tabla (y la nota "
            "del encabezado) dice con cuáles.",
        ),
        (_OPTIONAL_COLOR, "Azul", "Opcional. Vacía en un campo de alcance = todos."),
    ):
        guide.append([title, meaning])
        swatch = guide.cell(row=guide.max_row, column=1)
        swatch.fill = _fill(color)
        swatch.font = _HEADER_FONT
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
                _requirement_text(required.get(spec.name)),
                spec.help,
                ", ".join(spec.choices),
            ]
        )
    for letter, width in zip("ABCDE", (22, 26, 30, 70, 60), strict=True):
        guide.column_dimensions[letter].width = width

    _add_checks(book, sheet, columns, validation, len(rows))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


# The «Valores» sheet: one column per dropdown (title, then the values).
_LISTS = (
    ("kind", "Clase apoyo"),
    ("calc_method", "Tipo apoyo"),
    ("value_unit", "Unidad valor"),
    (VALIDATION, VALIDATION_HEADER),
    ("promo_warehouses", "Bodega promocional"),
    ("funding", "Quién financia"),
    ("settlement_method", "Forma de liquidación"),
)


def _list_values(name: str) -> list[str]:
    if name == "kind":
        return [vocabulary.KIND_LABELS[kind] for kind in vocabulary.ACTIVE_KINDS]
    if name == "calc_method":
        return _active_methods()
    if name == "value_unit":
        return ["Unidad", "Caja"]
    if name in (VALIDATION, "promo_warehouses"):
        return ["Sí", "No"]
    if name == "funding":
        return list(vocabulary.FUNDINGS)
    return list(vocabulary.SETTLEMENTS)


def _add_checks(
    book: Any, sheet: Any, columns: Sequence[tuple[str, str]], validation: bool, filled: int
) -> None:
    """Dropdowns from the «Valores» sheet, and date checks and the date format on the rows to
    fill (filled or not), so a typed date is a date and not a text."""

    values = book.create_sheet("Valores")
    ranges: dict[str, str] = {}
    for column, (name, title) in enumerate(_LISTS, start=1):
        items = _list_values(name)
        letter = get_column_letter(column)
        values.cell(row=1, column=column, value=title).font = Font(bold=True)
        values.column_dimensions[letter].width = 30
        for row, item in enumerate(items, start=2):
            values.cell(row=row, column=column, value=item)
        ranges[name] = f"Valores!${letter}$2:${letter}${len(items) + 1}"
    last = max(filled + 1, 1) + _VALIDATED_ROWS
    names = [name for name, _ in columns] + ([VALIDATION] if validation else [])
    for index, name in enumerate(names, start=1):
        letter = get_column_letter(index)
        target = f"{letter}2:{letter}{last}"
        if name in ranges:
            check = DataValidation(
                type="list",
                formula1=ranges[name],
                allow_blank=True,
                showErrorMessage=True,
                errorTitle="Valor no válido",
                error="Elige un valor de la lista",
            )
        elif fields.FIELDS.get(name) is not None and fields.FIELDS[name].type == fields.DATE:
            for row in range(2, last + 1):
                sheet.cell(row=row, column=index).number_format = _DATE_FORMAT
            check = DataValidation(
                type="date",
                operator="between",
                formula1="DATE(2000,1,1)",
                formula2="DATE(2100,12,31)",
                allow_blank=True,
                showErrorMessage=True,
                errorTitle="Fecha no válida",
                error="Escribe una fecha (dd/mm/aaaa). Fecha Fin vacía = sin fecha final.",
            )
        else:
            continue
        sheet.add_data_validation(check)
        check.add(target)


__all__ = [
    "SpreadsheetSupportReader",
    "column_requirements",
    "template_headers",
    "template_workbook",
]
