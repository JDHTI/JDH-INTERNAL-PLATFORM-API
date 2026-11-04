"""Bring into the directory what the intranet has and Entra does not.

Reads the intranet database (INTRANET-JDH-API, Prisma) and copies, by Entra object id:

- the sites (``sites``): created here by name when missing, except the placeholder
  "Sin sede asignada";
- per employee: position (``position``), birth date, site, manager flag and the DWH mark
  (``dwhSyncedAt``), the data loaded by hand or from the SIESA CSV;
- the employees without an Entra id, as manual employees (unless one with the same name
  already exists).

Run ``scripts/sync-directory.py`` first: synced employees are matched by object id.
Nothing is written without ``--apply``.

Usage:
    poetry run python scripts/import-intranet-directory.py "postgresql://user:pass@host:5432/intranet_jdh"
    poetry run python scripts/import-intranet-directory.py "<dsn>" --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg

from app.app_container import (
    build_application_container,
    initialize_global_resources,
    shutdown_global_resources,
)
from app.core.config import Settings
from app.modules.directory.domain.search import normalize_text

PLACEHOLDER_SITE = normalize_text("Sin sede asignada")


def _aware(value: datetime | None) -> datetime | None:
    """Prisma stores ``timestamp`` without zone, in UTC."""

    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _identifier(value: str) -> str:
    if not value.replace("_", "").isalnum():
        raise SystemExit(f"Esquema inválido: {value}")
    return f'"{value}"'


async def read_intranet(dsn: str, schema: str) -> tuple[list[Any], list[Any]]:
    """Sites and employees of the intranet, without the deleted ones."""

    connection = await asyncpg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        prefix = _identifier(schema)
        sites = await connection.fetch(
            f'SELECT name, address, "isActive" AS is_active FROM {prefix}.sites '
            'WHERE "deletedAt" IS NULL ORDER BY name'
        )
        employees = await connection.fetch(
            f"""
            SELECT e."entraId" AS entra_id, e."fullName" AS full_name, e.position,
                   e."birthDate" AS birth_date, e."isManager" AS is_manager,
                   e."dwhSyncedAt" AS dwh_synced_at, e."corporateEmail" AS email,
                   e.department, e."isActive" AS is_active, s.name AS site_name
            FROM {prefix}.employees e
            LEFT JOIN {prefix}.sites s ON s.id = e."siteId"
            WHERE e."deletedAt" IS NULL
            ORDER BY e."fullName"
            """
        )
        return list(sites), list(employees)
    finally:
        await connection.close()


async def run(dsn: str, schema: str, apply: bool) -> int:
    """Compare both directories and, with ``apply``, write the differences."""

    intranet_sites, intranet_employees = await read_intranet(dsn, schema)
    container = build_application_container(Settings())
    resources = await initialize_global_resources(container)
    report = {
        "sedes_nuevas": 0,
        "empleados_actualizados": 0,
        "empleados_sin_cambios": 0,
        "oid_sin_empleado": 0,
        "manuales_nuevos": 0,
        "manuales_existentes": 0,
    }
    unmatched: list[str] = []
    try:
        repository = container.v1_container.directory_repository()
        sites = {
            normalize_text(site.name): site.id
            for site in await repository.list_sites(include_inactive=True)
        }
        for row in intranet_sites:
            key = normalize_text(row["name"])
            if key == PLACEHOLDER_SITE or key in sites:
                continue
            report["sedes_nuevas"] += 1
            if apply:
                created = await repository.create_site(
                    " ".join(row["name"].split()), row["address"] or None, bool(row["is_active"])
                )
                sites[key] = created.id

        manual_names = {
            normalize_text(employee.full_name)
            for employee in (
                await repository.list_employees(
                    terms=[],
                    site_id=None,
                    department=None,
                    public_only=False,
                    include_inactive=True,
                    offset=0,
                    limit=100_000,
                )
            )[0]
        }
        for row in intranet_employees:
            site_id = sites.get(normalize_text(row["site_name"]))
            if row["entra_id"] is None:
                if normalize_text(row["full_name"]) in manual_names:
                    report["manuales_existentes"] += 1
                    continue
                report["manuales_nuevos"] += 1
                manual_names.add(normalize_text(row["full_name"]))
                if apply:
                    await repository.create_employee(
                        {
                            "full_name": " ".join(row["full_name"].split()),
                            "email": (row["email"] or "").lower() or None,
                            "position": row["position"] or "",
                            "department": row["department"],
                            "site_id": site_id,
                            "birth_date": row["birth_date"],
                            "is_manager": bool(row["is_manager"]),
                            "is_active": bool(row["is_active"]),
                            "dwh_synced_at": _aware(row["dwh_synced_at"]),
                        }
                    )
                continue
            employee = await repository.get_employee_by_oid(row["entra_id"])
            if employee is None:
                report["oid_sin_empleado"] += 1
                unmatched.append(f"{row['entra_id']}  {row['full_name']}")
                continue
            changes: dict[str, Any] = {}
            if row["position"] and row["position"] != employee.position:
                changes["position"] = row["position"]
            if row["birth_date"] and row["birth_date"] != employee.birth_date:
                changes["birth_date"] = row["birth_date"]
            if site_id and site_id != employee.site_id:
                changes["site_id"] = site_id
            if row["is_manager"] and not employee.is_manager:
                changes["is_manager"] = True
            synced_at = _aware(row["dwh_synced_at"])
            if synced_at and synced_at != employee.dwh_synced_at:
                changes["dwh_synced_at"] = synced_at
            if not changes:
                report["empleados_sin_cambios"] += 1
                continue
            report["empleados_actualizados"] += 1
            if apply:
                await repository.update_employee(employee.id, changes)
    finally:
        await shutdown_global_resources(resources)

    print("Importación aplicada" if apply else "Simulación (usa --apply para escribir)")
    print(f"  sedes en la intranet       {len(intranet_sites):>6}")
    print(f"  empleados en la intranet   {len(intranet_employees):>6}")
    for name, value in report.items():
        print(f"  {name:<26} {value:>6}")
    if unmatched:
        print("\nSin empleado con ese oid (ejecuta scripts/sync-directory.py primero):")
        for line in unmatched[:50]:
            print(f"  {line}")
        if len(unmatched) > 50:
            print(f"  ... y {len(unmatched) - 50} más")
    return 0


def main() -> int:
    """Parse arguments and run."""

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("dsn", help="PostgreSQL URL of the intranet database (intranet_jdh)")
    parser.add_argument("--schema", default="public", help="Schema of the intranet tables")
    parser.add_argument("--apply", action="store_true", help="Write the changes")
    args = parser.parse_args()
    return asyncio.run(run(args.dsn, args.schema, args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
