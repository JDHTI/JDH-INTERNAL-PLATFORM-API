"""Import the P.13.F.01 "Seguimiento Peticiones" spreadsheet into PQRSF cases.

Rows that share a consecutive become one case; rows with an empty consecutive
are more documents of the case above. Cases are numbered per site like any
other (P26AC0001), in order of reception; the sheet's consecutive stays in
``legacy.original_consecutive``.

Runs as a dry run unless ``--apply`` is given, and reruns skip rows already
loaded (each case remembers its source line).

Usage:
    poetry run python scripts/import-pqrsf-tracking.py P.13.F.csv --year 2026
    poetry run python scripts/import-pqrsf-tracking.py P.13.F.csv --year 2026 \
        --assignee "Cristian Camilo Garzón=cristian.garzon@jdh.com.co" \
        --site "Copacabana=medellin" --site "Funza=bogota" --apply
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.app_container import (
    build_application_container,
    initialize_global_resources,
    shutdown_global_resources,
)
from app.core.config import Settings
from app.core.errors import ApplicationError, NotFoundError
from app.modules.pqrsf.application.dto import ImportedCase, ImportedItem

SOURCE = "P.13.F.01"
_MONTHS = {
    "jan": 1, "ene": 1, "feb": 2, "mar": 3, "apr": 4, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dec": 12,
    "dic": 12,
}  # fmt: skip
_STATUS = {"terminado": "finalizada", "en proceso": "en_curso"}
# Column positions in the sheet (header row starts with "Fecha de Petición").
DATE, NUMBER, RESPONSIBLE, SUPPLIER, DEPENDENCY, REQUESTER, CUSTOMER, EXECUTIVE = range(8)
DOCUMENT, QUANTITY, SITE, RESPONDED, _AVG, SLA, _DIFF, STATUS, CAUSE, DETAIL = range(8, 18)


def normalize(value: str) -> str:
    """Lowercase ASCII letters and digits only, for tolerant matching."""

    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", ascii_value.lower())


def clean(value: str | None) -> str | None:
    """Collapse whitespace; ``None`` when blank."""

    text = " ".join((value or "").split())
    return text or None


def parse_date(value: str, year: int) -> tuple[date | None, bool]:
    """Parse ``2-Jan`` or ``07-Jul-26``; returns the date and whether it had a year."""

    parts = (value or "").strip().split("-")
    if len(parts) not in (2, 3) or not parts[0].isdigit():
        return None, False
    month = _MONTHS.get(parts[1][:3].lower())
    if month is None:
        return None, False
    explicit = len(parts) == 3 and parts[2].isdigit()
    if explicit:
        year = int(parts[2]) + (2000 if len(parts[2]) == 2 else 0)
    try:
        return date(year, month, int(parts[0])), explicit
    except ValueError:
        return None, False


def parse_int(value: str) -> int | None:
    """Integer cell or ``None``."""

    text = (value or "").strip()
    return int(text) if text.lstrip("-").isdigit() else None


@dataclass
class Group:
    """Rows of the sheet that form one case."""

    line: int
    number: int | None
    rows: list[tuple[int, list[str]]] = field(default_factory=list)
    original_number: int | None = None


def read_groups(path: Path) -> tuple[list[Group], Counter]:
    """Group sheet rows into cases."""

    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    stats: Counter = Counter()
    groups: list[Group] = []
    seen_numbers: set[int] = set()
    header_found = False
    current: Group | None = None
    for line, row in enumerate(csv.reader(text.splitlines()), start=1):
        row = (row + [""] * 18)[:18]
        if not header_found:
            header_found = normalize(row[0]).startswith("fechadepeticion")
            continue
        if not any(cell.strip() for cell in row[:12]):
            continue
        stats["rows"] += 1
        number = parse_int(row[NUMBER])
        if number is None:
            if current is None:
                current = Group(line, None)
                groups.append(current)
                stats["rows_without_case"] += 1
            current.rows.append((line, row))
            continue
        if current is not None and current.original_number == number:
            current.rows.append((line, row))
            continue
        current = Group(line, number if number not in seen_numbers else None, [], number)
        if number in seen_numbers:
            stats["repeated_consecutives"] += 1
        seen_numbers.add(number)
        current.rows.append((line, row))
        groups.append(current)
    if not header_found:
        raise SystemExit("No se encontró la fila de encabezado 'Fecha de Petición'")
    return groups, stats


def parse_pairs(values: list[str], option: str) -> dict[str, str]:
    """Parse repeated ``name=value`` options keyed by normalized name."""

    pairs: dict[str, str] = {}
    for value in values:
        name, _, target = value.partition("=")
        if not name.strip() or not target.strip():
            raise SystemExit(f"{option} debe tener la forma 'nombre=valor': {value}")
        pairs[normalize(name)] = target.strip()
    return pairs


async def run(args: argparse.Namespace) -> int:
    """Build the cases and load them (or report them in a dry run)."""

    groups, stats = read_groups(Path(args.csv))
    assignee_emails = parse_pairs(args.assignee, "--assignee")
    site_codes = parse_pairs(args.site, "--site")

    container = build_application_container(Settings())
    resources = await initialize_global_resources(container)
    try:
        v1 = container.v1_container
        tracking = v1.pqrsf_tracking_service()
        cases = v1.pqrsf_case_repository()
        directory = v1.user_directory()
        request_types = await v1.pqrsf_repository().list_request_types(active_only=False)
        request_type = next((t for t in request_types if t.code == args.request_type), None)
        if request_type is None:
            raise SystemExit(f"Tipo de solicitud {args.request_type} no encontrado")
        document_types = {normalize(d.name): d.id for d in await cases.list_document_types(True)}
        dependencies = {normalize(d.name): d.id for d in await cases.list_dependencies(True)}
        sites = await v1.site_service().list_sites(include_inactive=True)
        sites_by_code = {s.code: s.id for s in sites}
        sites_by_name = {normalize(s.name): s.id for s in sites} | {
            normalize(s.code): s.id for s in sites
        }
        people: dict[str, UUID | None] = {}
        missing: dict[str, Counter] = {
            k: Counter() for k in ("document", "site", "dependency", "responsible")
        }

        async def resolve_person(name: str | None) -> UUID | None:
            if not name:
                return None
            key = normalize(name)
            if key not in people:
                email = assignee_emails.get(key)
                people[key] = None
                if email:
                    try:
                        people[key] = (await directory.require_user(email)).id
                    except NotFoundError:
                        stats["assignee_not_signed_in"] += 1
            if people[key] is None:
                missing["responsible"][name] += 1
            return people[key]

        def resolve_site(name: str | None) -> UUID | None:
            if not name:
                return None
            key = normalize(name)
            site_id = sites_by_code.get(site_codes[key]) if key in site_codes else None
            site_id = site_id or sites_by_name.get(key)
            if site_id is None:
                missing["site"][name] += 1
            return site_id

        previous_date: date | None = None
        commands: list[ImportedCase] = []
        for group in groups:
            first = group.rows[0][1]
            received_on, _ = parse_date(first[DATE], args.year)
            if received_on is None:
                received_on = previous_date
                stats["cases_without_date"] += 1
            if received_on is None:
                stats["skipped_cases"] += 1
                continue
            previous_date = received_on

            items: list[ImportedItem] = []
            for _, row in group.rows:
                document = clean(row[DOCUMENT])
                quantity = parse_int(row[QUANTITY])
                if document is None and quantity is None:
                    continue
                document_type_id = document_types.get(normalize(document)) if document else None
                if document and document_type_id is None:
                    missing["document"][document] += 1
                responded_on, explicit = parse_date(row[RESPONDED], args.year)
                if responded_on and responded_on < received_on and not explicit:
                    responded_on = responded_on.replace(year=responded_on.year + 1)
                sla = parse_int(row[SLA])
                items.append(
                    ImportedItem(
                        document_type_id=document_type_id,
                        description=None if document_type_id else document,
                        quantity=quantity if quantity and quantity > 0 else None,
                        sla_days=sla if sla and sla > 0 else None,
                        responded_on=responded_on,
                        delay_cause=clean(row[CAUSE]),
                        delay_detail=clean(row[DETAIL]),
                    )
                )

            status_code = _STATUS.get(" ".join(first[STATUS].split()).lower())
            if status_code is None:
                answered = [i for i in items if i.responded_on]
                status_code = (
                    "finalizada"
                    if items and len(answered) == len(items)
                    else "en_curso"
                    if answered
                    else "recibida"
                )
            dependency = clean(first[DEPENDENCY])
            dependency_id = dependencies.get(normalize(dependency)) if dependency else None
            if dependency and dependency_id is None:
                missing["dependency"][dependency] += 1
            responsible = clean(first[RESPONSIBLE])
            site = clean(first[SITE])
            commands.append(
                ImportedCase(
                    source_key=f"{SOURCE}:{group.line}",
                    request_type_id=request_type.id,
                    number=None,
                    received_on=received_on,
                    status_code=status_code,
                    assignee_user_id=await resolve_person(responsible),
                    dependency_id=dependency_id,
                    site_id=resolve_site(site),
                    customer_name=clean(first[CUSTOMER]),
                    supplier_name=clean(first[SUPPLIER]),
                    sales_executive_name=clean(first[EXECUTIVE]),
                    requester_name=clean(first[REQUESTER]),
                    items=tuple(items),
                    legacy={
                        "source": SOURCE,
                        "lines": [line for line, _ in group.rows],
                        "original_consecutive": group.original_number,
                        "responsable": responsible,
                        "sucursal": site,
                        "estado": clean(first[STATUS]),
                    },
                )
            )
            stats["items"] += len(items)

        print(f"Filas con datos: {stats['rows']}")
        print(f"Casos: {len(commands)} | documentos: {stats['items']}")
        print(f"Consecutivos repetidos en la hoja: {stats['repeated_consecutives']}")
        print(f"Casos sin fecha (toman la del anterior): {stats['cases_without_date']}")
        print(f"Estados: {dict(Counter(c.status_code for c in commands))}")
        for kind, values in missing.items():
            if values:
                top = ", ".join(f"{name} ({count})" for name, count in values.most_common(10))
                print(f"Sin coincidencia de {kind}: {len(values)} valores -> {top}")
        if stats["assignee_not_signed_in"]:
            print(f"Responsables mapeados sin sesión iniciada: {stats['assignee_not_signed_in']}")

        if not args.apply:
            print("\nSimulación: no se guardó nada. Usa --apply para importar.")
            return 0
        # Oldest first, so each site's numbers follow the order of reception.
        ordered = sorted(commands, key=lambda c: c.received_on)
        created = skipped = 0
        failed: list[str] = []
        for command in ordered:
            try:
                stored = await tracking.import_case(command)
            except ApplicationError as exc:
                failed.append(f"{command.source_key}: {exc.detail}")
                continue
            if stored is None:
                skipped += 1
            else:
                created += 1
        print(f"\nImportados: {created} | ya existían: {skipped} | con error: {len(failed)}")
        for line in failed[:20]:
            print(f"  {line}")
        return 1 if failed else 0
    finally:
        await shutdown_global_resources(resources)


def main() -> None:
    """Parse command-line options and run the import."""

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("csv", help="CSV exported from the P.13.F.01 sheet")
    parser.add_argument("--year", type=int, required=True, help="Year of dates without year")
    parser.add_argument("--request-type", default="peticion", help="Request type code")
    parser.add_argument(
        "--assignee",
        action="append",
        default=[],
        help="Map a Responsable to a platform email: 'Nombre=correo' (repeatable)",
    )
    parser.add_argument(
        "--site",
        action="append",
        default=[],
        help="Map a Sucursal to a site code: 'Copacabana=medellin' (repeatable)",
    )
    parser.add_argument("--apply", action="store_true", help="Write the cases")
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
