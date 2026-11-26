"""The empty «Maestro Vendedores JDH» template (v4, 2026-11-11).

Moved from the supplier platform on 2026-11-26. dwh-platform's parser
(``src/dwh/pipelines/cargar_maestro_vendedores.py``) reads it: the header goes in ROW 1 of each
sheet and empty rows are skipped. If dwh-platform changes columns, this changes with it.

What the template helps with:
- Without the document column (N° DOCUMENTO in COMERCIALES JDH, CEDULA in Coordinadores and
  Directores), without UNIDAD NEGOCIO in COMERCIALES JDH, or with repeated headers, the whole
  upload answers 400.
- An empty document, or one that does not resolve in the ERP, rejects only that row.
- REGIONAL and C.O are matched by name to the operation center and then to t280; empty or
  unknown ends in VARIOS.
- UNIDAD NEGOCIO is free text: a new name creates a new commercial unit.

So the lists warn instead of blocking, and every column is text (a numeric document loses
its leading zeros).
"""

from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

FILENAME = "Plantilla Maestro Vendedores JDH.xlsx"

SHEETS: dict[str, list[str]] = {
    "COMERCIALES JDH": [
        "N° DOCUMENTO",
        "NOMBRE VENDEDOR",
        "EQUIPO",
        "CLASE",
        "ZONA",
        "CIUDAD",
        "UNIDAD NEGOCIO",
        "REGIONAL",
        "COORDINADOR",
        "DIRECTOR",
        "CORREO",
        "CELULAR",
        "OBSERVACION",
        "NOMBRE GRUPO CORREOS",
        "GRUPO CORREOS",
        "C. COSTO",
    ],
    "Coordinadores": [
        "CEDULA",
        "COORDINADOR",
        "EQUIPO",
        "C.O",
        "DIRECTOR",
        "CORREO",
        "CELULAR",
        "OBSERVACION",
    ],
    "Directores": ["CEDULA", "DIRECTOR", "C.O", "CORREO", "CELULAR", "OBSERVACION"],
}

REGIONALES = [
    "ACO URABA",
    "ANTIOQUIA",
    "BOGOTA",
    "CARTAGENA",
    "CENTRO",
    "COSTA",
    "OCCIDENTE",
    "ORIENTE",
    "URABA",
    "VALLEDUPAR",
    "VARIOS",
]
# A help, not a closed list: dwh-platform creates a unit for a new name and normalizes the MIX
# ones (MIX FOOD-MASIVO -> FOOD SERVICE / MASIVO, MIX RETAIL-MASIVO -> RETAIL / MASIVO).
UNIDADES_NEGOCIO = [
    "FOOD SERVICE",
    "FOOD SERVICE / MASIVO",
    "FOOD SERVICE / MASIVO FOOD",
    "MARKET",
    "MASIVO",
    "RETAIL",
    "RETAIL / MASIVO",
    "VARIOS",
    "MIX RETAIL-MASIVO",
    "MIX FOOD-MASIVO",
]
EMPTY_ROWS = 300

INSTRUCTIONS = [
    ("Cómo diligenciar", None),
    (
        "1.",
        "No cambies los nombres de las hojas ni los encabezados de la fila 1. Si falta la "
        "columna del documento, o la de UNIDAD NEGOCIO en COMERCIALES JDH, o hay encabezados "
        "repetidos, se rechaza el archivo completo.",
    ),
    (
        "2.",
        "Hoja COMERCIALES JDH: una fila por vendedor. N° DOCUMENTO es la cédula o NIT del "
        "vendedor en el ERP, escrita como texto para no perder ceros a la izquierda. Si está "
        "vacío o no corresponde a un vendedor, esa fila se rechaza.",
    ),
    ("3.", "C. COSTO debe existir en el ERP; si no, la fila se rechaza."),
    (
        "4.",
        "Coordinadores y Directores: una fila por persona, con su CEDULA. COORDINADOR y "
        "DIRECTOR en la hoja de vendedores se cruzan por nombre con cualquiera de estas dos "
        "hojas; N/A se ignora.",
    ),
    (
        "5.",
        "UNIDAD NEGOCIO (solo en COMERCIALES JDH): elige de la lista. Un nombre que no esté en "
        "la lista crea una unidad de negocio nueva. MIX FOOD-MASIVO se guarda como FOOD "
        "SERVICE / MASIVO y MIX RETAIL-MASIVO como RETAIL / MASIVO.",
    ),
    (
        "6.",
        "Coordinadores: C.O es la regional del coordinador. Directores: C.O es informativa (un "
        "director opera a nivel nacional).",
    ),
    ("Importante", None),
    (
        "!",
        "La carga ESCRIBE DIRECTAMENTE en el datawarehouse y actualiza a cada vendedor que "
        "venga en el archivo.",
    ),
    (
        "!",
        "Si REGIONAL (o C.O) queda VACÍA o MAL ESCRITA, la persona queda en la regional "
        "VARIOS. Elige siempre un valor de la lista desplegable.",
    ),
    (
        "!",
        "Sube siempre la plantilla COMPLETA. Los vendedores que no vengan en el archivo no se "
        "modifican.",
    ),
    ("Regionales", ", ".join(REGIONALES)),
    ("Unidades de negocio", ", ".join(UNIDADES_NEGOCIO)),
]

# Column -> (column in the Listas sheet, values, title, help, message when not in the list).
_DROPDOWNS = {
    "REGIONAL": (
        "A",
        REGIONALES,
        "REGIONAL",
        "Vacía o mal escrita = la persona queda en VARIOS.",
        "Ese nombre no está en la lista: quedará en la regional VARIOS.",
    ),
    "C.O": (
        "A",
        REGIONALES,
        "C.O",
        "Centro de operación o regional. Vacío o mal escrito = VARIOS.",
        "Ese nombre no está en la lista: si no es un centro de operación, quedará en la "
        "regional VARIOS.",
    ),
    "UNIDAD NEGOCIO": (
        "B",
        UNIDADES_NEGOCIO,
        "UNIDAD NEGOCIO",
        "Elige de la lista. Un nombre nuevo crea otra unidad de negocio.",
        "Esa unidad no existe: se creará una unidad de negocio nueva.",
    ),
}
_HEAD = PatternFill("solid", fgColor="1F3864")


def vendor_master_template() -> bytes:
    """The empty workbook to fill and upload."""

    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.properties.title = "Maestro Vendedores JDH"
    for sheet_name, columns in SHEETS.items():
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(columns)
        for index, header in enumerate(columns, start=1):
            cell = sheet.cell(1, index)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = _HEAD
            letter = get_column_letter(index)
            sheet.column_dimensions[letter].width = max(14, len(header) + 4)
            for row in range(2, EMPTY_ROWS + 2):
                sheet.cell(row, index).number_format = "@"
            if header in _DROPDOWNS:
                column, values, title, help_text, error_text = _DROPDOWNS[header]
                rule = DataValidation(
                    type="list",
                    formula1=f"=Listas!${column}$1:${column}${len(values)}",
                    allow_blank=True,
                    errorStyle="warning",
                    errorTitle=f"{title} no reconocida",
                    error=error_text,
                    promptTitle=title,
                    prompt=help_text,
                    showErrorMessage=True,
                    showInputMessage=True,
                )
                rule.add(f"{letter}2:{letter}{EMPTY_ROWS + 1}")
                sheet.add_data_validation(rule)
        sheet.freeze_panes = "C2" if sheet_name == "COMERCIALES JDH" else "A2"
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(columns))}1"
    lists = workbook.create_sheet("Listas")
    for column, values, *_ in {spec[0]: spec for spec in _DROPDOWNS.values()}.values():
        for row, name in enumerate(values, start=1):
            lists[f"{column}{row}"] = name
    lists.sheet_state = "hidden"
    guide = workbook.create_sheet("Instrucciones")
    guide.column_dimensions["A"].width = 20
    guide.column_dimensions["B"].width = 100
    for row, (label, body) in enumerate(INSTRUCTIONS, start=1):
        guide.cell(row, 1, label).font = Font(
            bold=True, color="9C0006" if label == "!" else "000000"
        )
        if body:
            guide.cell(row, 2, body).alignment = Alignment(wrap_text=True, vertical="top")
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


__all__ = ["FILENAME", "SHEETS", "vendor_master_template"]
