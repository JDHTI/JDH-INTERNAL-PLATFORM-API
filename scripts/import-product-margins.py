"""Load the margin master (``Maestro Margen.xlsx``) into the products.

Reads one margin per channel for every product from the sheet ``Margen Producto`` (a margin
of 0 means "none defined" and is stored as empty) and the liquor consumption tax
(``IMPTOLIC``) with its Costa value and tax group from ``Margen Proveedor-Impuestos``.
Products must already be in the platform (``scripts/sync-dwh-masters.py``). Runs as a dry
run unless ``--apply`` is given and can be repeated: only differences are written, and a
margin edited by hand is replaced by the file's value.

The file can also be uploaded from the app (``POST /commercial/products/margins``).

Usage:
    poetry run python scripts/import-product-margins.py "Maestro Margen.xlsx"
    poetry run python scripts/import-product-margins.py "Maestro Margen.xlsx" --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import openpyxl

from app.app_container import (
    build_application_container,
    initialize_global_resources,
    shutdown_global_resources,
)
from app.core.config import Settings
from app.core.errors import ApplicationError
from app.modules.commercial_masters.application.service import IMPORT_ACTOR
from app.modules.commercial_masters.domain.margins import normalize
from app.modules.commercial_masters.infrastructure.margins_file import SpreadsheetMarginReader


def _number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except ArithmeticError:
        return None
    return number if number > 0 else None


def _code(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip() or None


def read_liquor_taxes(path: Path, sheet: str, tax_sheet: str) -> dict[str, dict[str, Any]]:
    """``{sku: {liquor_tax, liquor_tax_coast, liquor_tax_group}}`` of the taxed products."""

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        taxes: dict[str, dict[str, Any]] = {}
        wanted = normalize(sheet)
        for item in book.worksheets:
            if normalize(item.title) != wanted:
                continue
            rows = item.iter_rows(values_only=True)
            header = [normalize(cell) for cell in next(rows)]
            sku, tax = header.index("referencia"), header.index("imptolic")
            for row in rows:
                value = _number(row[tax])
                if value is not None and _code(row[sku]):
                    taxes[_code(row[sku])] = {"liquor_tax": value}
        wanted = normalize(tax_sheet)
        for item in book.worksheets:
            if normalize(item.title) != wanted:
                continue
            columns: dict[str, int] = {}
            for row in item.iter_rows(values_only=True):
                names = {normalize(cell): index for index, cell in enumerate(row) if cell}
                if not columns:
                    if "sku" in names:
                        columns = names
                    continue
                key = _code(row[columns["sku"]])
                if key is None:
                    continue
                entry = taxes.setdefault(key, {})
                entry.setdefault("liquor_tax", _number(row[columns["nacional"]]))
                entry["liquor_tax_coast"] = _number(row[columns["costa"]])
                entry["liquor_tax_group"] = _code(row[columns["grupo impositivo"]])
        return taxes
    finally:
        book.close()


async def run(path: Path, sheet: str, tax_sheet: str, apply: bool) -> int:
    """Load margins and liquor taxes and print what changed."""

    container = build_application_container(Settings())
    resources = await initialize_global_resources(container)
    try:
        service = container.v1_container.commercial_master_service()
        repository = container.v1_container.commercial_master_repository()
        rows = SpreadsheetMarginReader().read(path.name, path.read_bytes(), sheet)
        report = await service.apply_margin_rows(rows, IMPORT_ACTOR, apply=apply)

        taxes = read_liquor_taxes(path, sheet, tax_sheet)
        known = await repository.margins_by_sku(list(taxes))
        updates = [
            {
                "sku": sku,
                "liquor_tax": values.get("liquor_tax"),
                "liquor_tax_coast": values.get("liquor_tax_coast"),
                "liquor_tax_group": values.get("liquor_tax_group"),
            }
            for sku, values in taxes.items()
            if sku in known
        ]
        if apply and updates:
            await repository.bulk_update_products(updates, IMPORT_ACTOR, datetime.now(UTC))
    except (ApplicationError, OSError, KeyError, ValueError) as exc:
        print(f"Error: {getattr(exc, 'detail', None) or exc}", file=sys.stderr)
        return 1
    finally:
        await shutdown_global_resources(resources)

    mode = "APLICADO" if apply else "SIMULACIÓN (usa --apply para escribir)"
    print(f"Márgenes {mode}")
    print(f"  filas leídas ............ {report.rows_read}")
    print(f"  productos a actualizar .. {report.updated}")
    print(f"  sin cambios ............. {report.unchanged}")
    print(f"  referencias repetidas ... {report.duplicated}")
    print(f"  sin producto en maestro . {len(report.unknown_skus)}")
    print(f"  filas inválidas ......... {len(report.issues)}")
    for issue in report.issues[:10]:
        print(f"    fila {issue.row_number} {issue.sku}: {issue.problem}")
    if report.unknown_skus:
        print(f"    sin producto: {', '.join(report.unknown_skus[:10])}")
    print(f"Impuesto al licor: {len(taxes)} en el libro, {len(updates)} con producto en el maestro")
    return 0


def main() -> int:
    """Parse arguments and run."""

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("workbook", type=Path, help="Path of the margin master .xlsx")
    parser.add_argument("--sheet", default="Margen Producto", help="Sheet with the margins")
    parser.add_argument("--tax-sheet", default="Margen Proveedor-Impuestos")
    parser.add_argument("--apply", action="store_true", help="Write; default is a dry run.")
    args = parser.parse_args()
    return asyncio.run(run(args.workbook, args.sheet, args.tax_sheet, args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
