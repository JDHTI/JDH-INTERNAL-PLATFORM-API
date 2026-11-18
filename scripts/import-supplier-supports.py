"""Import ``1. MAESTRO APOYOS PROVEEDORES.xlsx`` into the supplier supports master.

Every table of the workbook is read with its own adapter (see
``app/modules/supplier_supports/infrastructure/workbook.py``), cleaned, bound to
the real suppliers, products and clients, and stored as one normalized support.
Rows with problems (impossible dates, unknown NIT or SKU, duplicated rows,
drag-filled NITs, ambiguous bases...) are imported as ``pendiente_revision`` with
their issues, so nothing in the workbook is lost.

Also loads the supplier SKU catalogs (``Maestro_Sku_*``). Margins live on each product:
see ``scripts/import-product-margins.py``.

Needs the commercial masters in the platform first (``scripts/sync-dwh-masters.py``).
Runs as a dry run unless ``--apply`` is given; reruns skip supports already loaded
(each remembers its sheet and row).

Usage:
    poetry run python scripts/import-supplier-supports.py "1. MAESTRO APOYOS PROVEEDORES.xlsx"
    poetry run python scripts/import-supplier-supports.py FILE.xlsx --only Familia --only Kimberly
    poetry run python scripts/import-supplier-supports.py FILE.xlsx --report issues.csv --apply
    poetry run python scripts/import-supplier-supports.py FILE.xlsx
        --supplier "ASCENDER=412" --apply
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.app_container import (
    build_application_container,
    initialize_global_resources,
    shutdown_global_resources,
)
from app.core.config import Settings
from app.core.errors import ApplicationError
from app.modules.supplier_supports.domain.special_cases import SpecialCaseData
from app.modules.supplier_supports.infrastructure import workbook as wb

# Particular client cases that cannot be configured from the workbook alone: they are opened
# as special cases (requests) and their supports point at them.
SPECIAL_CASES = {
    ("Rubbermaid", "Lista_Summar_RCP"): (
        "575",
        SpecialCaseData(
            title="Rubbermaid: costo apoyado exclusivo para Summar",
            request=(
                "El nuevo costo apoyado (costo RCP) de la lista de Summar aplica solo para este "
                "cliente (NIT 800125313). Confirmar sucursales, vigencia y cómo se factura."
            ),
            rule_to_program=(
                "Si cliente = Summar y el SKU está en la lista: costo = COSTO APOYADO; para el "
                "resto de clientes se sigue usando el COSTO REGULAR."
            ),
            customer_nit="800125313",
            customer_name="SUMMAR",
            priority="media",
            source_key="Rubbermaid/Lista_Summar_RCP",
        ),
    ),
    ("Rubbermaid", "Lista_SodexoCedi_RCP"): (
        "575",
        SpecialCaseData(
            title="Rubbermaid: costo apoyado exclusivo para Sodexo CEDI",
            request=(
                "Misma regla que Summar para Sodexo CEDI, pero el libro no trae NIT. Definir qué "
                "sucursal(es) de Sodexo S.A.S. (NIT 800230447) son el CEDI y configurarlas a mano. "
                "La vigencia del libro (2025-04-01 a 2026-03-30) ya venció: confirmar si se "
                "renueva."
            ),
            rule_to_program=(
                "Si cliente = Sodexo CEDI y el SKU está en la lista: costo = COSTO APOYADO; el "
                "resto de clientes usa el COSTO REGULAR."
            ),
            customer_name="SODEXO CEDI",
            priority="alta",
            source_key="Rubbermaid/Lista_SodexoCedi_RCP",
        ),
    ),
}


class CachedLookup:
    """Memoize master lookups: the workbook repeats the same few thousand keys."""

    def __init__(self, lookup) -> None:
        self.lookup = lookup
        self.products: dict[str, object] = {}
        self.suppliers: dict[str, object] = {}
        self.customers: dict[str, str] = {}
        self.missing_customers: set[str] = set()

    async def products_by_sku(self, skus):
        missing = [sku for sku in skus if sku not in self.products]
        if missing:
            found = await self.lookup.products_by_sku(missing)
            self.products.update({sku: found.get(sku) for sku in missing})
        return {sku: self.products[sku] for sku in skus if self.products.get(sku)}

    async def commercial_suppliers_by_code(self, codes):
        missing = [code for code in codes if code not in self.suppliers]
        if missing:
            found = await self.lookup.commercial_suppliers_by_code(missing)
            self.suppliers.update({code: found.get(code) for code in missing})
        return {code: self.suppliers[code] for code in codes if self.suppliers.get(code)}

    async def commercial_supplier_names(self, ids):
        return await self.lookup.commercial_supplier_names(ids)

    async def commercial_suppliers_by_name(self, query):
        return await self.lookup.commercial_suppliers_by_name(query)

    async def customers(self, nits):
        return await self.lookup.customers(nits)

    async def customer_names(self, nits):
        missing = [
            nit for nit in nits if nit not in self.customers and nit not in self.missing_customers
        ]
        if missing:
            found = await self.lookup.customer_names(missing)
            self.customers.update(found)
            self.missing_customers.update(nit for nit in missing if nit not in found)
        return {nit: self.customers[nit] for nit in nits if nit in self.customers}


async def resolve_supplier(ref, overrides, lookup, notes):
    """Code of a supplier reference; ``name:X`` is matched against the supplier names."""

    if ref is None or not ref.startswith("name:"):
        return ref
    name = ref[5:].strip()
    if name in overrides:
        return overrides[name]
    if name in notes:
        return notes[name]
    matches = await lookup.commercial_suppliers_by_name(name)
    notes[name] = matches[0].code if matches else None
    return notes[name]


async def run(args: argparse.Namespace) -> int:
    """Read the workbook, bind it to the masters and import (or simulate)."""

    path = Path(args.workbook)
    try:
        book = wb.Workbook(path)
    except FileNotFoundError:
        print(f"No existe el archivo: {path}", file=sys.stderr)
        return 1
    overrides = dict(item.split("=", 1) for item in args.supplier)
    container = build_application_container(Settings())
    resources = await initialize_global_resources(container)
    try:
        service = container.v1_container.supplier_support_service()
        raw_lookup = container.v1_container.master_lookup()
        lookup = CachedLookup(raw_lookup)
        service.masters = lookup
        probe = await lookup.commercial_suppliers_by_code(["233", "399", "8"])
        if not probe:
            print(
                "No hay proveedores comerciales cargados: ejecuta primero "
                "scripts/sync-dwh-masters.py --apply",
                file=sys.stderr,
            )
            return 1

        cases = container.v1_container.special_case_service()
        case_ids: dict[tuple[str, str], object] = {}
        for key, (supplier_code, case) in SPECIAL_CASES.items():
            if args.only and key[0] not in args.only:
                continue
            case_ids[key] = await cases.ensure(
                case, supplier_code=supplier_code, dry_run=not args.apply
            )
            print(f"Caso especial {key[0]}/{key[1]}: {case_ids[key] or 'por crear'}")

        notes: dict[str, str | None] = {}
        created = skipped = flagged = updated = manual = 0
        per_table: dict[tuple[str, str], Counter] = defaultdict(Counter)
        issue_rows: list[list[str]] = []
        unresolved: Counter = Counter()

        if not args.only:
            await import_catalogs(book, raw_lookup, overrides, lookup, notes, args.apply)

        for item in wb.read_supports(book, args.only or None):
            code = await resolve_supplier(item.supplier, overrides, lookup, notes)
            if item.supplier and item.supplier.startswith("name:") and code is None:
                unresolved[item.supplier[5:]] += 1
            try:
                data = item.data
                case_id = case_ids.get((item.sheet, item.table))
                if case_id is not None:
                    data = replace(data, special_case_id=case_id)
                outcome = await service.import_support(
                    data, supplier_code=code, dry_run=not args.apply, refresh=args.refresh
                )
            except ApplicationError as exc:
                print(f"  ! {item.data.source_key}: {exc.detail}", file=sys.stderr)
                per_table[(item.sheet, item.table)]["errores"] += 1
                continue
            counts = per_table[(item.sheet, item.table)]
            if outcome.skipped_manual:
                manual += 1
                counts["editados a mano (no se tocan)"] += 1
                continue
            if outcome.updated:
                updated += 1
                counts["actualizados"] += 1
            elif not outcome.created:
                skipped += 1
                counts["ya cargados"] += 1
                continue
            else:
                created += 1
                counts["leídos"] += 1
            if outcome.issues:
                flagged += 1
                counts["con inconsistencias"] += 1
            for issue in outcome.issues:
                issue_rows.append(
                    [
                        item.sheet,
                        item.table,
                        item.data.source_key or "",
                        item.data.customer_nit or "",
                        item.data.product_sku or "",
                        issue.code,
                        issue.field or "",
                        issue.detail or "",
                    ]
                )
    finally:
        await shutdown_global_resources(resources)

    mode = "APLICADO" if args.apply else "SIMULACIÓN (usa --apply para escribir)"
    print(f"Importación {mode}")
    for (sheet, table), counts in per_table.items():
        detail = "  ".join(f"{name}={count}" for name, count in counts.items())
        print(f"  {sheet}/{table}: {detail}")
    print(
        f"Total: {created} nuevos, {updated} actualizados, {manual} editados a mano (omitidos), "
        f"{flagged} con inconsistencias, {skipped} ya cargados"
    )
    for name, code in notes.items():
        print(f"  proveedor '{name}' -> {code or 'SIN CRUCE (usa --supplier NOMBRE=CODIGO)'}")
    if unresolved:
        print(f"  filas sin proveedor: {dict(unresolved)}")
    issue_counts = Counter(row[5] for row in issue_rows)
    if issue_counts:
        print("Inconsistencias:", dict(issue_counts))
    if args.report:
        with open(args.report, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["hoja", "tabla", "origen", "nit", "sku", "inconsistencia", "campo", "detalle"]
            )
            writer.writerows(issue_rows)
        print(f"Reporte de inconsistencias: {args.report} ({len(issue_rows)} filas)")
    return 0


async def import_catalogs(book, raw_lookup, overrides, lookup, notes, apply) -> None:
    """Supplier SKU catalogs (the margin now lives on each product; see import-product-margins)."""

    codes = wb.read_supplier_codes(book)
    resolved = []
    for ref, code in codes:
        supplier = await resolve_supplier(ref, overrides, lookup, notes)
        if supplier:
            resolved.append(
                wb.SupplierProductCode(
                    supplier,
                    code.supplier_sku,
                    code.product_sku,
                    code.pack_factor,
                    code.kg_per_unit,
                    code.notes,
                )
            )
    if apply:
        saved, missing = await raw_lookup.save_supplier_product_codes(resolved)
        print(f"Códigos de proveedor: {saved} guardados, {len(missing)} sin producto en el maestro")
    else:
        print(f"Códigos de proveedor: {len(resolved)} listos para guardar")


def main() -> int:
    """Parse arguments and run."""

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("workbook", help="Path of the .xlsx workbook")
    parser.add_argument("--only", action="append", default=[], help="Sheet to import (repeatable)")
    parser.add_argument(
        "--supplier",
        action="append",
        default=[],
        metavar="NOMBRE=CODIGO",
        help="Force the commercial supplier code of a name-matched sheet (repeatable)",
    )
    parser.add_argument("--report", help="CSV file for the inconsistencies found")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-read supports already loaded and update the ones nobody has edited by hand",
    )
    parser.add_argument("--apply", action="store_true", help="Write; default is a dry run.")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
