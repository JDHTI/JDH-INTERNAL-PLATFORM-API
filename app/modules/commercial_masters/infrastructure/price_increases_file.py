"""Reads the purchasing area's price increase workbook (``Acumulado Alza Precios``).

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
from collections.abc import Callable
from typing import Any

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

from app.core.errors import InvalidInputError
from app.modules.commercial_masters.domain.margins import normalize
from app.modules.commercial_masters.domain.price_increases import (
    CellError,
    CellNote,
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


__all__ = ["MAX_ROWS", "PriceIncreaseFileReader"]
