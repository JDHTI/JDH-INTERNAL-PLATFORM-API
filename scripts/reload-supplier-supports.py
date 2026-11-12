"""Reload the supplier supports from a folder of per-supplier files, by key.

The folder holds ``Apoyos Proveedor - X.xlsx`` files: the tables of the old master workbook,
one file per supplier, with the supports added, changed and gone since the first import.
Each row is matched to the stored support by its key (supplier, class, NIT, SKU, agreement,
start date): new keys are created, known ones updated with what the file carries (an approved
JDH contribution is never replaced) or left as they are. Supports of the same tables that the
files no longer bring are reported and, with ``--expire-missing``, closed the day before.

Nothing is discarded: unknown NITs or SKUs leave the support ``pendiente_revision`` with its
issue. Each file is one load (``supplier_support_loads``) written in one transaction, and every
support it touches is published to the DWH.

A dry run unless ``--apply``.

Usage:
    poetry run python scripts/reload-supplier-supports.py "Apoyos proveedores"
    poetry run python scripts/reload-supplier-supports.py FOLDER --report reload.csv
    poetry run python scripts/reload-supplier-supports.py FOLDER --expire-missing --apply
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.app_container import (  # noqa: E402
    build_application_container,
    initialize_global_resources,
    shutdown_global_resources,
)
from app.core.config import Settings  # noqa: E402
from app.modules.supplier_supports.application.reload import (  # noqa: E402
    ReloadRow,
    ReloadTable,
    SupportReloadService,
    provided_fields,
)
from app.modules.supplier_supports.infrastructure import workbook as wb  # noqa: E402

_SOURCES = {f"{source.sheet}/{source.table}": source for source in wb.SOURCES}


async def supplier_code(ref: str | None, overrides: dict[str, str], lookup, notes) -> str | None:
    """Code of a supplier reference; ``name:X`` is matched against the supplier names."""

    if ref is None or not ref.startswith("name:"):
        return ref
    name = ref[5:].strip()
    if name in overrides:
        return overrides[name]
    if name not in notes:
        matches = await lookup.commercial_suppliers_by_name(name)
        notes[name] = matches[0].code if matches else None
    return notes[name]


async def run(args: argparse.Namespace) -> int:
    """Read the folder, plan every file and write it (or only report)."""

    try:
        book = wb.FolderWorkbook(Path(args.folder))
    except FileNotFoundError:
        print(f"No hay libros .xlsx en: {args.folder}", file=sys.stderr)
        return 1
    overrides = dict(item.split("=", 1) for item in args.supplier)
    container = build_application_container(Settings())
    resources = await initialize_global_resources(container)
    notes: dict[str, str | None] = {}
    issue_rows: list[list[str]] = []
    try:
        service = container.v1_container.supplier_support_service()
        reload = SupportReloadService(service)
        lookup = container.v1_container.master_lookup()
        by_file: dict[Path, list[ReloadTable]] = defaultdict(list)
        rows_by_label: dict[str, list[ReloadRow]] = defaultdict(list)
        for item in wb.read_supports(book, args.only or None, skip_missing=True):
            label = f"{item.sheet}/{item.table}"
            source = _SOURCES[label]
            code = await supplier_code(item.supplier, overrides, lookup, notes)
            rows_by_label[label].append(
                ReloadRow(
                    item.data,
                    code,
                    provided_fields(item.data, [*source.cols, *source.fixed]),
                )
            )
        for label, rows in rows_by_label.items():
            path = book.file_of(_SOURCES[label].table)
            by_file[path].append(ReloadTable(label, tuple(rows)))

        totals: dict[str, int] = defaultdict(int)
        for path, tables in sorted(by_file.items()):
            report = await reload.plan(path, tables, expire_missing=args.expire_missing)
            if args.apply:
                report = await reload.apply(report)
            loaded = f" carga {report.result.load_id}" if report.result else ""
            print(f"{path.name}{loaded}")
            for table in report.tables:
                print(
                    f"  {table.label}: filas={table.rows} crear={table.create} "
                    f"actualizar={table.update} sin_cambio={table.unchanged} "
                    f"ausentes={table.missing} vencer={table.expire} "
                    f"con_inconsistencias={table.flagged} llave_repetida={table.repeated}"
                )
                if table.changed_fields:
                    changed = ", ".join(
                        f"{name}={count}" for name, count in table.changed_fields.most_common(8)
                    )
                    print(f"    cambian: {changed}")
                for name in ("rows", "create", "update", "unchanged", "missing", "expire"):
                    totals[name] += getattr(table, name)
                for nit, sku, issue in table.issues:
                    issue_rows.append(
                        [
                            path.name,
                            table.label,
                            nit or "",
                            sku or "",
                            issue.code,
                            issue.field or "",
                            issue.detail or "",
                        ]
                    )
    finally:
        await shutdown_global_resources(resources)

    mode = "APLICADO" if args.apply else "SIMULACIÓN (usa --apply para escribir)"
    print(f"Recarga {mode}: " + ", ".join(f"{name}={count}" for name, count in totals.items()))
    for name, code in notes.items():
        print(f"  proveedor '{name}' -> {code or 'SIN CRUCE (usa --supplier NOMBRE=CODIGO)'}")
    if args.report:
        with open(args.report, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["archivo", "tabla", "nit", "sku", "inconsistencia", "campo", "detalle"]
            )
            writer.writerows(issue_rows)
        print(f"Reporte de inconsistencias: {args.report} ({len(issue_rows)} filas)")
    return 0


def main() -> int:
    """Parse arguments and run."""

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", help="Folder with the supplier .xlsx files")
    parser.add_argument("--only", action="append", default=[], help="Sheet to reload (repeatable)")
    parser.add_argument(
        "--supplier",
        action="append",
        default=[],
        metavar="NOMBRE=CODIGO",
        help="Force the commercial supplier code of a name-matched sheet (repeatable)",
    )
    parser.add_argument("--report", help="CSV file for the inconsistencies found")
    parser.add_argument(
        "--expire-missing",
        action="store_true",
        help="Close (the day before) the supports in force the files no longer bring",
    )
    parser.add_argument("--apply", action="store_true", help="Write; default is a dry run.")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
