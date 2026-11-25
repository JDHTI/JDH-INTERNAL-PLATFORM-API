"""Reads the list update template an advisor uploads back (2026-11-25).

The template is the workbook of ``GET /quotations/{id}/list-template.xlsx``: sheet
``Lineas`` with one row per reference and sheet ``Cotizacion`` with the header. The advisor
deletes the rows of the references to leave out of the update; only the ``Referencia``
column is read (prices and the rest are ignored), plus the price list of the header to make
sure the file belongs to that list.

The workbook is opened read-only (rows stream, no cell objects kept), and the caller runs
this in a worker thread.
"""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal
from typing import Any

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

from app.core.errors import InvalidInputError
from app.modules.quotations.domain.ports import TemplateContent
from app.modules.quotations.infrastructure.lines_workbook import (
    HEADER_SHEET,
    LINES_SHEET,
    LIST_ROW,
    SKU_COLUMN,
)

# A SIESA list has at most some thousands of references; anything far beyond is not the
# template (a whole sales report pasted in) and is refused before it is read entirely.
MAX_ROWS = 20_000
_NOT_TEMPLATE = (
    f"No es la plantilla de la actualización: falta la hoja {LINES_SHEET} con la columna "
    f"{SKU_COLUMN}. Descárgala de nuevo y borra solo filas"
)


def _text(value: Any) -> str | None:
    """A cell as the text of a reference: numbers typed again in Excel come back whole."""

    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, float | Decimal) and value == int(value):
        value = int(value)
    text = str(value).strip()
    return text or None


def _fold(value: Any) -> str:
    return (_text(value) or "").casefold()


class ListTemplateFile:
    """Reads the references and the price list of an uploaded template."""

    def read(self, content: bytes) -> TemplateContent:
        """Raises:
        InvalidInputError: Not a workbook, no ``Lineas`` sheet with ``Referencia``, or
            more rows than any list has.
        """

        try:
            book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except (InvalidFileException, zipfile.BadZipFile, OSError, KeyError, ValueError) as exc:
            raise InvalidInputError("El archivo no es un libro de Excel válido") from exc
        try:
            if LINES_SHEET not in book.sheetnames:
                raise InvalidInputError(_NOT_TEMPLATE)
            skus = self._skus(book[LINES_SHEET])
            price_list_id = (
                self._price_list(book[HEADER_SHEET]) if HEADER_SHEET in book.sheetnames else None
            )
        finally:
            book.close()
        return TemplateContent(skus=skus, price_list_id=price_list_id)

    @staticmethod
    def _skus(sheet: Any) -> tuple[str, ...]:
        rows = sheet.iter_rows(values_only=True)
        header = next(rows, None) or ()
        wanted = SKU_COLUMN.casefold()
        column = next((i for i, value in enumerate(header) if _fold(value) == wanted), None)
        if column is None:
            raise InvalidInputError(_NOT_TEMPLATE)
        found: dict[str, None] = {}
        for count, row in enumerate(rows, start=1):
            if count > MAX_ROWS:
                raise InvalidInputError(
                    f"La hoja {LINES_SHEET} pasa de {MAX_ROWS} filas: no es la plantilla"
                )
            sku = _text(row[column]) if column < len(row) else None
            if sku:
                found[sku] = None
        return tuple(found)

    @staticmethod
    def _price_list(sheet: Any) -> str | None:
        wanted = LIST_ROW.casefold()
        for row in sheet.iter_rows(max_row=50, max_col=2, values_only=True):
            if row and _fold(row[0]) == wanted:
                return _text(row[1]) if len(row) > 1 else None
        return None


__all__ = ["MAX_ROWS", "ListTemplateFile"]
