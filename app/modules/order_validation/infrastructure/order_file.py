"""Reads the retained orders report (SIESA export) from ``.xlsx`` or ``.csv``.

Columns are found by their header, not by position, and the header row may sit below a
title. A cell that cannot be read never drops its row: the value stays empty and the row
carries a ``problem``, so the validator leaves that line pending instead of losing it.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

from app.core.errors import InvalidInputError
from app.modules.commercial_masters.domain.entities import clean_nit
from app.modules.order_validation.domain.entities import OrderLine

# Normalized header -> field. Normalized = no accents, lower case, words only.
HEADERS: dict[str, str] = {
    "c o factura": "operation_center",
    "co factura": "operation_center",
    "centro de operacion": "operation_center",
    "fecha": "order_date",
    "fecha pedido": "order_date",
    "nro documento": "document",
    "numero documento": "document",
    "documento": "document",
    "pedido": "document",
    "estado docto": "status_text",
    "estado documento": "status_text",
    "cliente factura": "customer_nit",
    "nit cliente": "customer_nit",
    "nit": "customer_nit",
    "razon social cliente factura": "customer_name",
    "razon social cliente": "customer_name",
    "razon social": "customer_name",
    "nombre vendedor cliente": "salesperson",
    "vendedor": "salesperson",
    "canal": "channel",
    "bodega": "warehouse",
    "referencia": "sku",
    "referencia jdh": "sku",
    "sku": "sku",
    "desc item": "description",
    "descripcion item": "description",
    "descripcion": "description",
    "precio unit": "unit_price",
    "precio unitario": "unit_price",
    "cant pedida": "quantity",
    "cantidad pedida": "quantity",
    "cantidad": "quantity",
    "margen promedio": "margin",
    "margen": "margin",
    "proveedor": "supplier",
    "lista de precios fact": "price_list",
    "lista de precios": "price_list",
    "retenido margen": "retained",
    "vlr imp imptolic": "liquor_tax_value",
    "valor impuesto licor": "liquor_tax_value",
    "impuesto licor": "liquor_tax_value",
    "imptolic": "liquor_tax_value",
}

REQUIRED = {
    "document": "Nro documento",
    "customer_nit": "Cliente factura",
    "sku": "Referencia",
    "unit_price": "Precio unit.",
    "margin": "Margen promedio",
    "supplier": "Proveedor",
}

EXTENSIONS = ("xlsx", "xlsm", "csv")
# The header must show up within the first rows; below that the file is not this report.
HEADER_SCAN_ROWS = 15
_SUPPLIER = re.compile(r"^\s*(\d+)\s*-\s*(.*?)\s*$")
_WORDS = re.compile(r"[^a-z0-9]+")


def normalize_header(value: object) -> str:
    """``"Razón social cliente factura"`` -> ``"razon social cliente factura"``."""

    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(_WORDS.sub(" ", text.lower()).split())


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = " ".join(str(value).split())
    return text or None


def _code(value: Any) -> str | None:
    """A reference or document code: ``18294.0`` read by Excel is ``18294``."""

    text = _text(value)
    if text and text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def parse_decimal(value: Any) -> Decimal | None:
    """A number written by Excel or by a person (``118.628``, ``1.234,5``, ``12,5%``).

    Raises:
        ValueError: If the cell has text that is not a number.
    """

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(value)
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value))
    text = str(value).strip().replace("$", "").replace("%", "").replace(" ", "")
    # A lone dash is how the accounting format writes a zero.
    if not text or text in ("-", "–", "—"):
        return None
    if "," in text and "." in text:
        # The last separator is the decimal one.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        whole, _, fraction = text.rpartition(",")
        text = text.replace(",", "") if len(fraction) == 3 and whole else text.replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(value) from exc


def parse_date(value: Any) -> date | None:
    """A date cell, or ``dd/mm/yyyy``, ``yyyy-mm-dd`` and ``yyyy/mm/dd`` text.

    Raises:
        ValueError: If the cell has text that is not a date.
    """

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().split(" ")[0]
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise ValueError(value)


def _supplier(value: Any) -> tuple[str | None, str | None]:
    """``"359 - LOCERIA COLOMBIANA S.A."`` -> ``("359", "LOCERIA COLOMBIANA S.A.")``."""

    text = _text(value)
    if text is None:
        return None, None
    match = _SUPPLIER.match(text)
    if match:
        return match.group(1), match.group(2) or None
    code = _code(text)
    return (code, None) if code and code.isdigit() else (None, text)


def _header_map(rows: Sequence[Sequence[Any]]) -> tuple[int, dict[str, int]] | None:
    """The first row that names the required columns, and the position of each field."""

    for index, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        positions: dict[str, int] = {}
        for column, cell in enumerate(row):
            field = HEADERS.get(normalize_header(cell))
            if field and field not in positions:
                positions[field] = column
        if len(positions) >= 3:
            return index, positions
    return None


def _line(row_number: int, row: Sequence[Any], positions: dict[str, int]) -> OrderLine | None:
    def cell(field: str) -> Any:
        column = positions.get(field)
        return row[column] if column is not None and column < len(row) else None

    document = _code(cell("document"))
    sku = _code(cell("sku"))
    if document is None and sku is None:
        return None
    problems: list[str] = []

    def number(field: str, label: str) -> Decimal | None:
        try:
            return parse_decimal(cell(field))
        except ValueError:
            problems.append(f"{label} ilegible: {cell(field)!r}")
            return None

    try:
        order_date = parse_date(cell("order_date"))
    except ValueError:
        order_date = None
        problems.append(f"Fecha ilegible: {cell('order_date')!r}")
    margin_points = number("margin", "Margen")
    liquor_tax_value = number("liquor_tax_value", "Impuesto al licor")
    supplier_code, supplier_name = _supplier(cell("supplier"))
    return OrderLine(
        row_number=row_number,
        document=document or f"(sin documento, fila {row_number})",
        customer_nit=clean_nit(cell("customer_nit")),
        sku=sku,
        supplier_code=supplier_code,
        supplier_name=supplier_name,
        unit_price=number("unit_price", "Precio"),
        quantity=number("quantity", "Cantidad"),
        # The report writes the margin in percentage points (2.2212 = 2,22 %).
        margin_pct=None if margin_points is None else margin_points / 100,
        order_date=order_date,
        operation_center=_code(cell("operation_center")),
        status_text=_text(cell("status_text")),
        customer_name=_text(cell("customer_name")),
        salesperson=_text(cell("salesperson")),
        channel=_text(cell("channel")),
        warehouse=_text(cell("warehouse")),
        description=_text(cell("description")),
        price_list=_code(cell("price_list")),
        retained=_text(cell("retained")),
        liquor_tax_value=liquor_tax_value,
        problems=tuple(problems),
    )


def lines_from_rows(rows: Iterable[Sequence[Any]]) -> list[OrderLine]:
    """Order lines from the rows of a sheet (header included).

    Raises:
        InvalidInputError: If no header row is found or required columns are missing.
    """

    materialized = [list(row) for row in rows]
    found = _header_map(materialized)
    if found is None:
        raise InvalidInputError(
            "No se encontró la fila de encabezados del reporte de pedidos retenidos "
            f"(se esperan columnas como {', '.join(REQUIRED.values())})"
        )
    header_index, positions = found
    missing = [label for field, label in REQUIRED.items() if field not in positions]
    if missing:
        raise InvalidInputError(f"Faltan columnas en el archivo: {', '.join(missing)}")
    lines = []
    for offset, row in enumerate(materialized[header_index + 1 :], start=header_index + 2):
        line = _line(offset, row, positions)
        if line is not None:
            lines.append(line)
    return lines


def _xlsx_rows(content: bytes) -> list[list[Any]]:

    try:
        book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, OSError, KeyError, ValueError) as exc:
        raise InvalidInputError("El archivo no es un libro de Excel válido") from exc
    try:
        # The report is the first sheet that has the header; extra sheets are ignored.
        for sheet in book.worksheets:
            rows = [list(row) for row in sheet.iter_rows(values_only=True)]
            if _header_map(rows) is not None:
                return rows
        return [list(row) for row in book.worksheets[0].iter_rows(values_only=True)]
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
    sample = text[:4096]
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect)]


class SpreadsheetOrderReader:
    """Reads the report from Excel (``.xlsx``/``.xlsm``) or CSV."""

    def read(self, filename: str, content: bytes) -> list[OrderLine]:
        """Order lines of the uploaded file.

        Raises:
            InvalidInputError: If the format is not supported or the report is not there.
        """

        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if extension not in EXTENSIONS:
            raise InvalidInputError(
                f"Formato no soportado: .{extension or '?'} (usa {', '.join(EXTENSIONS)}; "
                "un .xls se guarda antes como .xlsx)"
            )
        rows = _csv_rows(content) if extension == "csv" else _xlsx_rows(content)
        return lines_from_rows(rows)


__all__ = ["SpreadsheetOrderReader", "lines_from_rows", "parse_date", "parse_decimal"]
