"""The supplier's «MAESTRA» workbook: written for download and read back when it returns.

The layout is the area's ``MAESTRA 50 - ASCENDER S.A. 23-11-2026.xlsx`` (form P.17.F.04):

- rows 1-3: the SIG header with the JDH logo, the form code, version and date;
- rows 4-7: the instructions; row 8: the columns; data from row 9 (frozen there);
- the formulas of the cost with the healthy-food tax, the variation and the increase state,
  with the same colors for SUBIO, BAJO and ESTABLE;
- a hidden ``Motivos`` sheet, as the area keeps it.

Changes to the area's workbook:

- the ICUI comes from SIESA instead of a lookup in ``Motivos`` (2026-11-23);
- the formulas leave a row blank until it has a new cost, instead of calling every untouched
  row «BAJO»;
- the «Regional» list adds ``RESTO DE REGIONALES`` and every row starts at ``NACIONAL``, and a
  «Precio Venta Público sin IVA (PVP)» column follows it (2026-11-24; the discount column per
  regional of 2026-11-23 is gone).

The reader matches the columns by name, so the supplier may reorder or hide them; only the
reference and the new cost without tax are required. The workbook's formulas are never
trusted: the service works the costs out again.
"""

from __future__ import annotations

import io
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.drawing.image import Image
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from app.core.errors import InvalidInputError
from app.modules.commercial_masters.domain.price_increases import (
    CellError,
    clean_text,
    parse_date,
    parse_money,
    parse_ratio,
)
from app.modules.costs.domain.increases import (
    NATIONAL,
    OBSERVATIONS,
    SCOPES,
    LineInput,
    RowIssue,
    SupplierItem,
    WorkbookRows,
    as_fraction,
    clean_scope,
)

SHEET = "Maestra_Costos"
REASONS_SHEET = "Motivos"
HEADER_ROW = 8
FIRST_ROW = 9
FORM_CODE = "P.17.F.04"
FORM_VERSION = "01"
_LOGO = Path(__file__).parent / "assets" / "logo-jdh.png"
_EMU_PER_PIXEL = 9525

_GREY = "FFAFABAB"
_GREEN = "FF00B050"
_RED = "FFFF0000"
_YELLOW = "FFFFFF00"
_LIGHT_GREEN = "FF92D050"


@dataclass(frozen=True)
class Column:
    header: str
    width: float
    fill: str
    number_format: str = "General"


REGIONAL = "Regional"
PUBLIC_PRICE = "Precio Venta Público sin IVA (PVP)"

# The MAESTRA columns, in order (A..Y): the area's, with the PVP after the regional.
COLUMNS: tuple[Column, ...] = (
    Column("Fecha de Incremento", 9.8, _GREY, "dd/mm/yyyy"),
    Column("SKU Proveedor", 15.9, _GREEN, "@"),
    Column("Codigo de Barras EAN 13", 14.1, _GREEN, "@"),
    Column("Nit", 13.2, _GREY, "@"),
    Column("Referencia", 12.4, _GREY, "@"),
    Column("Proveedor", 35.1, _GREY),
    Column("Desc. item", 36.9, _GREY),
    Column("Unidad de Empaque", 9.8, _GREY),
    Column("Estado el Item", 11.5, _GREY),
    Column("Categoria", 22.0, _GREY),
    Column("Grupo Impositivo", 14.1, _GREY, "@"),
    Column("Descuento Regular", 13.2, _GREY, "0.00%"),
    Column(REGIONAL, 14.1, _GREY),
    Column(PUBLIC_PRICE, 12.6, _YELLOW, "$ #,##0"),
    Column("ICUI (Indique el porcentaje)", 15.0, _RED, "0%"),
    Column("IBUA (Indique el valor por unidad)", 11.5, _RED, "$ #,##0.00"),
    Column("Costo anterior con Impuesto Saludable", 13.2, _GREY, "$ #,##0"),
    Column("Costo anterior sin Impuesto Saludable", 10.6, _RED, "$ #,##0"),
    Column("Costo Nuevo sin Impuesto Saludable", 9.8, _YELLOW, "$ #,##0"),
    Column("Costo nuevo con Impuesto Saludable", 11.5, _RED, "$ #,##0"),
    Column("variacion $", 10.6, _RED, "$ #,##0.00"),
    Column("Variacion %", 8.0, _RED, "0.00%"),
    Column("Estado Incremento", 11.5, _RED),
    Column("OBSERVACIONES", 14.1, _RED),
    Column("Estado ", 8.0, _LIGHT_GREEN),
)
_INDEX = {column.header: position for position, column in enumerate(COLUMNS, start=1)}
LAST_COLUMN = get_column_letter(len(COLUMNS))

INSTRUCTIONS = (
    "* Diligencie la Fecha de Incremento y el Costo Nuevo sin Impuesto Saludable de cada "
    "referencia que cambia; las filas sin costo nuevo no se cargan\n"
    "* Si el costo es el mismo en todo el país, deje la Regional en NACIONAL. Si cambia solo en "
    "una regional, cambie NACIONAL por esa regional: las demás conservan su costo\n"
    "* Si una regional tiene un costo propio y las demás comparten otro, deje una fila con esa "
    "regional y copie la fila con RESTO DE REGIONALES para las demás\n"
    "* Favor completar y organizar los codigos de barra y Sku Proveedor. Los costos en caso de "
    "tener decimales indique maximo 2"
)


def _letter(header: str) -> str:
    return get_column_letter(_INDEX[header])


def workbook_name(supplier_code: str, supplier_name: str | None, day: date) -> str:
    """``MAESTRA 50 - ASCENDER S.A. 23-11-2026.xlsx``: the area's way of naming it."""

    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", (supplier_name or "").strip())
    name = " ".join(name.split()) or "PROVEEDOR"
    return f"MAESTRA {supplier_code} - {name} {day:%d-%m-%Y}.xlsx"


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _header(sheet, day: date) -> None:
    thin = Side(style="thin")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    bold = Font(name="Arial", size=13, bold=True)
    for cells in ("A1:B3", "C1:S2", "C3:S3", "T1:W1", "T2:W2", "T3:W3", f"A4:{LAST_COLUMN}7"):
        sheet.merge_cells(cells)
    for cell, text in (
        ("C1", "SISTEMA INTEGRADO DE GESTIÓN"),
        ("C3", "NOTIFICACION CAMBIO DE PRECIOS PROVEEDORES JDH"),
        ("T1", f"Código: {FORM_CODE}"),
        ("T2", f"Versión: {FORM_VERSION}"),
        ("T3", f"Fecha: {day:%d/%m/%Y}"),
    ):
        sheet[cell] = text
        sheet[cell].font = bold if cell.startswith("C") else Font(name="Arial", bold=True)
        sheet[cell].alignment = Alignment(horizontal="center", vertical="center")
    for row in sheet.iter_rows(min_row=1, max_row=3, min_col=1, max_col=23):
        for cell in row:
            cell.border = box
    sheet["A4"] = INSTRUCTIONS
    sheet["A4"].font = Font(name="Calibri", size=11, bold=True)
    sheet["A4"].alignment = Alignment(vertical="top", wrap_text=True)
    for row, height in ((1, 19.5), (2, 19.5), (3, 19.5), (4, 18), (5, 18), (6, 18), (7, 18)):
        sheet.row_dimensions[row].height = height
    if _LOGO.exists():
        logo = Image(str(_LOGO))
        size = XDRPositiveSize2D(66 * _EMU_PER_PIXEL, 69 * _EMU_PER_PIXEL)
        logo.anchor = OneCellAnchor(
            _from=AnchorMarker(col=0, colOff=70 * _EMU_PER_PIXEL, row=0), ext=size
        )
        sheet.add_image(logo)


def _columns(sheet) -> None:
    thin = Side(style="thin")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    for position, column in enumerate(COLUMNS, start=1):
        cell = sheet.cell(HEADER_ROW, position, column.header)
        cell.fill = PatternFill("solid", fgColor=column.fill)
        cell.font = Font(name="Arial", size=8, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = box
        sheet.column_dimensions[get_column_letter(position)].width = column.width
    sheet.row_dimensions[HEADER_ROW].height = 41.25
    sheet.freeze_panes = f"A{FIRST_ROW}"


def _formulas(row: int) -> dict[str, str]:
    icui, ibua = (
        _letter("ICUI (Indique el porcentaje)"),
        _letter("IBUA (Indique el valor por unidad)"),
    )
    old, new = (
        _letter("Costo anterior sin Impuesto Saludable"),
        _letter("Costo Nuevo sin Impuesto Saludable"),
    )
    old_tax, new_tax = (
        _letter("Costo anterior con Impuesto Saludable"),
        _letter("Costo nuevo con Impuesto Saludable"),
    )
    amount, pct = _letter("variacion $"), _letter("Variacion %")
    return {
        old_tax: f'=IF({old}{row}="","",({old}{row}*(1+{icui}{row})+{ibua}{row}))',
        new_tax: f'=IF({new}{row}="","",({new}{row}*(1+{icui}{row})+{ibua}{row}))',
        amount: f'=IF(OR({new_tax}{row}="",{old_tax}{row}=""),"",+{new_tax}{row}-{old_tax}{row})',
        pct: f'=IF({amount}{row}="","",IFERROR({amount}{row}/{old_tax}{row},0))',
        _letter("Estado Incremento"): (
            f'=IF({amount}{row}="","",IF({amount}{row}<0,"BAJO",IF({amount}{row}=0,"ESTABLE","SUBIO")))'
        ),
    }


def _checks(sheet, last_row: int) -> None:
    end = max(last_row, FIRST_ROW + 500)

    def add(validation: DataValidation, header: str) -> None:
        letter = _letter(header)
        validation.add(f"{letter}{FIRST_ROW}:{letter}{end}")
        sheet.add_data_validation(validation)

    add(
        DataValidation(
            type="date",
            operator="between",
            formula1="DATE(2020,1,1)",
            formula2="DATE(2100,12,31)",
            allow_blank=True,
            showErrorMessage=True,
            error="Escriba una fecha válida (dd/mm/aaaa)",
        ),
        "Fecha de Incremento",
    )
    add(
        DataValidation(
            type="decimal",
            operator="greaterThan",
            formula1="0",
            allow_blank=True,
            showErrorMessage=True,
            error="El costo debe ser un número mayor que cero",
        ),
        "Costo Nuevo sin Impuesto Saludable",
    )
    add(
        DataValidation(
            type="decimal",
            operator="greaterThan",
            formula1="0",
            allow_blank=True,
            showErrorMessage=True,
            error="El PVP sin IVA debe ser un número mayor que cero",
        ),
        PUBLIC_PRICE,
    )
    add(
        DataValidation(
            type="decimal",
            operator="between",
            formula1="0",
            formula2="1",
            allow_blank=True,
            showErrorMessage=True,
            error="El descuento es un porcentaje entre 0 % y 100 %",
        ),
        "Descuento Regular",
    )
    add(
        DataValidation(
            type="list",
            formula1=f'"{",".join(SCOPES)}"',
            allow_blank=True,
            showErrorMessage=True,
            error="Elija NACIONAL, una regional o RESTO DE REGIONALES",
        ),
        REGIONAL,
    )
    add(
        DataValidation(type="list", formula1=f'"{",".join(OBSERVATIONS)}"', allow_blank=True),
        "OBSERVACIONES",
    )
    add(DataValidation(type="list", formula1='"SI,NO"', allow_blank=True), "Estado ")
    state = _letter("Estado Incremento")
    target = f"{state}{FIRST_ROW}:{state}{end}"
    for word, color in (("BAJO", "FFFF0000"), ("SUBIO", "FF00B050"), ("ESTABLE", "FFFFFF00")):
        sheet.conditional_formatting.add(
            target,
            CellIsRule(
                operator="equal", formula=[f'"{word}"'], fill=PatternFill("solid", bgColor=color)
            ),
        )


def template_workbook(items: Sequence[SupplierItem], reasons: Sequence[str], day: date) -> bytes:
    """The «MAESTRA» workbook of a supplier, one row per active product.

    Args:
        items (Sequence[SupplierItem]): The supplier's active products and old costs.
        reasons (Sequence[str]): The reasons of the catalog, for the hidden ``Motivos`` sheet.
        day (date): The date of the form.

    Returns:
        bytes: The ``.xlsx``.
    """

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = SHEET
    _header(sheet, day)
    _columns(sheet)
    text_columns = {
        "SKU Proveedor",
        "Codigo de Barras EAN 13",
        "Nit",
        "Referencia",
        "Grupo Impositivo",
    }
    for offset, item in enumerate(items):
        row = FIRST_ROW + offset
        values: dict[str, Any] = {
            "SKU Proveedor": item.supplier_sku,
            "Codigo de Barras EAN 13": item.ean,
            "Nit": item.nit,
            "Referencia": item.sku,
            "Proveedor": item.supplier_label,
            "Desc. item": item.description,
            "Unidad de Empaque": _number(item.pack_factor),
            "Estado el Item": item.state,
            "Categoria": item.category,
            "Grupo Impositivo": item.tax_group,
            REGIONAL: NATIONAL,
            "ICUI (Indique el porcentaje)": _number(item.icui_pct) or 0,
            "IBUA (Indique el valor por unidad)": _number(item.ibua),
            "Costo anterior sin Impuesto Saludable": _number(item.old_cost),
        }
        for header, value in values.items():
            if value is not None:
                sheet.cell(row, _INDEX[header], value)
        for letter, formula in _formulas(row).items():
            sheet[f"{letter}{row}"] = formula
        for position, column in enumerate(COLUMNS, start=1):
            cell = sheet.cell(row, position)
            cell.number_format = column.number_format if column.header not in text_columns else "@"
            cell.font = Font(name="Arial", size=8)
        sheet.row_dimensions[row].height = 11.25
    _checks(sheet, FIRST_ROW + len(items) - 1)
    motives = book.create_sheet(REASONS_SHEET)
    motives.append(["MOTIVOS"])
    for name in reasons:
        motives.append([name])
    motives.sheet_state = "hidden"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


# --- Reading ---


def _key(text: Any) -> str:
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return " ".join(plain.lower().replace("\n", " ").split())


_FIELDS = {
    _key("Referencia"): "sku",
    _key("Referencia JDH"): "sku",
    _key("SKU JDH"): "sku",
    _key("Fecha de Incremento"): "increased_on",
    _key("SKU Proveedor"): "supplier_sku",
    _key("Codigo de Barras EAN 13"): "ean",
    _key("ICUI (Indique el porcentaje)"): "icui_pct",
    _key("IBUA (Indique el valor por unidad)"): "ibua",
    _key("Costo Nuevo sin Impuesto Saludable"): "new_cost",
    _key("Descuento Regular"): "regular_discount_pct",
    _key(REGIONAL): "regional",
    _key(PUBLIC_PRICE): "public_price",
    _key("Precio Venta Publico sin IVA"): "public_price",
    _key("PVP"): "public_price",
    _key("PVP sin IVA"): "public_price",
    _key("OBSERVACIONES"): "observation",
    _key("Estado"): "accepted",
}
_REQUIRED = ("sku", "new_cost")
_LABELS = {"sku": "Referencia", "new_cost": "Costo Nuevo sin Impuesto Saludable"}


class IncreaseWorkbookReader:
    """Reads the supplier's returned «MAESTRA»."""

    def read(self, filename: str | None, content: bytes) -> WorkbookRows:
        """The rows with a new cost and the problems of each row.

        Args:
            filename (str | None): Name of the upload (``.xlsx`` or ``.xlsm``).
            content (bytes): The workbook.

        Returns:
            WorkbookRows: The lines and the issues.

        Raises:
            InvalidInputError: Not a workbook, or without the required columns.
        """

        if filename and not filename.lower().endswith((".xlsx", ".xlsm")):
            raise InvalidInputError("El archivo debe ser un Excel (.xlsx)")
        try:
            book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as exc:
            raise InvalidInputError("No se pudo leer el archivo como Excel") from exc
        try:
            sheet = book[SHEET] if SHEET in book.sheetnames else book.worksheets[0]
            rows = list(sheet.iter_rows(values_only=True))
        finally:
            book.close()
        header_at, columns = self._header(rows)
        lines: list[LineInput] = []
        issues: list[RowIssue] = []
        rows_read = 0
        for offset, values in enumerate(rows[header_at + 1 :], start=header_at + 2):
            cells = {
                field: values[index] if index < len(values) else None
                for field, index in columns.items()
            }
            if all(value in (None, "") for value in cells.values()):
                continue
            rows_read += 1
            parsed, problems = self._row(cells)
            sku = parsed.get("sku")
            for problem in problems:
                issues.append(RowIssue(offset, sku, problem))
            if parsed.get("new_cost") is None or problems:
                continue
            lines.append(LineInput(row_number=offset, **parsed))
        return WorkbookRows(rows_read, tuple(lines), tuple(issues))

    def _header(self, rows: list[tuple]) -> tuple[int, dict[str, int]]:
        for position, values in enumerate(rows[:20]):
            columns: dict[str, int] = {}
            for index, value in enumerate(values):
                field = _FIELDS.get(_key(value))
                if field and field not in columns:
                    columns[field] = index
            if all(name in columns for name in _REQUIRED):
                return position, columns
        missing = ", ".join(_LABELS.values())
        raise InvalidInputError(f"El archivo no tiene las columnas obligatorias: {missing}")

    def _row(self, cells: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        problems: list[str] = []
        parsed: dict[str, Any] = {}
        sku = clean_text(cells.get("sku"))
        parsed["sku"] = sku
        try:
            new_cost = parse_money(cells.get("new_cost"))
        except CellError:
            new_cost = None
            problems.append("El costo nuevo no es un número")
        if new_cost is None:
            return {"sku": sku}, problems
        if new_cost <= 0:
            problems.append("El costo nuevo debe ser mayor que cero")
        if new_cost != round(new_cost, 2):
            new_cost = round(new_cost, 2)
        parsed["new_cost"] = new_cost
        if not sku:
            problems.append("Falta la referencia")
        try:
            parsed["increased_on"] = parse_date(cells.get("increased_on"))
        except CellError:
            problems.append("La fecha de incremento no es una fecha")
        if (
            parsed.get("increased_on") is None
            and "La fecha de incremento no es una fecha" not in problems
        ):
            problems.append("Falta la fecha de incremento")
        parsed["supplier_sku"] = clean_text(cells.get("supplier_sku"))
        parsed["ean"] = clean_text(cells.get("ean"))
        for field, label in (
            ("icui_pct", "El ICUI"),
            ("regular_discount_pct", "El descuento regular"),
        ):
            try:
                value = as_fraction(parse_ratio(cells.get(field)))
            except CellError:
                problems.append(f"{label} no es un porcentaje")
                continue
            if value is not None and not 0 <= value <= 1:
                problems.append(f"{label} debe estar entre 0 % y 100 %")
            parsed[field] = value
        try:
            ibua = parse_money(cells.get("ibua"))
        except CellError:
            ibua = None
            problems.append("El IBUA no es un número")
        if ibua is not None and ibua < 0:
            problems.append("El IBUA no puede ser negativo")
        parsed["ibua"] = ibua
        try:
            public_price = parse_money(cells.get("public_price"))
        except CellError:
            public_price = None
            problems.append("El PVP sin IVA no es un número")
        if public_price is not None and public_price <= 0:
            problems.append("El PVP sin IVA debe ser mayor que cero")
        parsed["public_price"] = None if public_price is None else round(public_price, 2)
        raw_regional = clean_text(cells.get("regional"))
        regional = clean_scope(raw_regional)
        if regional is None:
            problems.append(f"Regional desconocida: {raw_regional}; elija una de la lista")
        else:
            parsed["regional"] = regional
        observation = clean_text(cells.get("observation"))
        parsed["observation"] = observation.upper()[:500] if observation else None
        state = clean_text(cells.get("accepted"))
        parsed["accepted"] = (state or "").strip().upper() != "NO"
        return parsed, problems


__all__ = [
    "COLUMNS",
    "FIRST_ROW",
    "HEADER_ROW",
    "SHEET",
    "IncreaseWorkbookReader",
    "template_workbook",
    "workbook_name",
]
