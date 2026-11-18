"""Delete every supplier support: a clean supports master (BI, 2026-11-18).

Only the supports go. What goes with them, because the database needs it:

- their tiers, DWH costs and document links (``ON DELETE CASCADE``);
- their JDH events (``supplier_support_jdh_events``): the audit trigger writes one per insert
  and edit of every support and the table is immutable (``protect_support_jdh_history``), so
  the trigger is switched off for this transaction only;
- their pending events in ``dwh_outbox`` (topic ``supplier_support``), so the publisher never
  sends a support that no longer exists. Nothing is sent to the DWH.

What stays: bulk loads, supplier profiles, special cases, the commercial masters (customers,
products, margins) and the internal requests from quotations (their items lose the link to
the support, ``ON DELETE SET NULL``; so do the order validation lines).

JDH requests (``supplier_support_requests``) point to a support and block the delete. The
script stops and counts them; ``--include-requests`` deletes them too (their protection trigger,
``protect_support_request``, is switched off for this transaction only).

Every row deleted is written to a CSV backup first. A dry run unless ``--apply``; ``--apply``
also asks to type the number of supports to delete.

Usage (run the platform backup first: deploy/backup):
    poetry run python scripts/clear-supplier-supports.py
    poetry run python scripts/clear-supplier-supports.py --apply --backup /backups/apoyos.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.infrastructure.database import Database  # noqa: E402

# (table, filter to the supports) counted and backed up before the delete.
_RELATED = (
    ("supplier_support_tiers", "support_id IS NOT NULL"),
    ("supplier_support_costs", "support_id IS NOT NULL"),
    ("supplier_support_documents", "support_id IS NOT NULL"),
    ("supplier_support_jdh_events", "support_id IS NOT NULL"),
    ("supplier_support_requests", "support_id IS NOT NULL"),
    ("dwh_outbox", "topic = 'supplier_support' AND status <> 'publicado'"),
)


def _plain(value: Any) -> Any:
    if isinstance(value, dict | list):
        return json.dumps(value, default=str, ensure_ascii=False)
    return value


async def _backup(session: Any, table: str, where: str, folder: Path, stamp: str) -> int:
    rows = (await session.execute(text(f"SELECT * FROM {table} WHERE {where}"))).mappings().all()
    path = folder / f"{stamp}-{table}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        if rows:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            for row in rows:
                writer.writerow({key: _plain(value) for key, value in row.items()})
    return len(rows)


async def main(apply: bool, folder: Path, include_requests: bool, confirm: str | None) -> int:
    database = Database(Settings().DATABASE_URL.get_secret_value())
    try:
        async with database.session_factory() as session:
            total = await session.scalar(text("SELECT count(*) FROM supplier_supports"))
            print(f"Apoyos: {total}")
            counts = {}
            for table, where in _RELATED:
                counts[table] = await session.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE {where}")
                )
                print(f"  {table}: {counts[table]}")
            if not total:
                print("No hay apoyos: nada que borrar.")
                return 0
            requests = counts["supplier_support_requests"]
            if requests and not include_requests:
                print(
                    f"Hay {requests} solicitudes JDH que apuntan a apoyos y bloquean el borrado. "
                    "Agrega --include-requests para borrarlas también (quedan en el respaldo)."
                )
                return 1
            if not apply:
                print("Simulación: nada se borró. Usa --apply para borrar (con respaldo).")
                return 0
            if confirm != str(total):
                print(f"Para confirmar escribe --confirm {total} (el número de apoyos).")
                return 1

            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            saved = await _backup(session, "supplier_supports", "true", folder, stamp)
            for table, where in _RELATED:
                await _backup(session, table, where, folder, stamp)
            print(f"Respaldo en {folder.resolve()} ({saved} apoyos y sus tablas)")

            # One transaction: the reads above opened it. The trigger is off only inside it.
            await session.execute(
                text(
                    "DELETE FROM dwh_outbox WHERE topic = 'supplier_support' "
                    "AND status <> 'publicado'"
                )
            )
            await session.execute(
                text(
                    "ALTER TABLE supplier_support_jdh_events "
                    "DISABLE TRIGGER protect_support_jdh_history"
                )
            )
            await session.execute(text("DELETE FROM supplier_support_jdh_events"))
            if include_requests:
                # Requests are immutable too (protect_support_request): off for this delete.
                await session.execute(
                    text(
                        "ALTER TABLE supplier_support_requests "
                        "DISABLE TRIGGER protect_support_request"
                    )
                )
                await session.execute(text("DELETE FROM supplier_support_requests"))
                await session.execute(
                    text(
                        "ALTER TABLE supplier_support_requests "
                        "ENABLE TRIGGER protect_support_request"
                    )
                )
            await session.execute(
                text(
                    "ALTER TABLE supplier_support_jdh_events "
                    "ENABLE TRIGGER protect_support_jdh_history"
                )
            )
            deleted = (await session.execute(text("DELETE FROM supplier_supports"))).rowcount
            await session.commit()
            left = await session.scalar(text("SELECT count(*) FROM supplier_supports"))
            print(f"Borrados {deleted} apoyos. Quedan {left}.")
            return 0
    finally:
        await database.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--apply", action="store_true", help="Borrar (por defecto, simula)")
    parser.add_argument("--confirm", help="El número de apoyos a borrar, para confirmar")
    parser.add_argument(
        "--include-requests",
        action="store_true",
        help="Borrar también las solicitudes JDH que apuntan a los apoyos",
    )
    parser.add_argument(
        "--backup",
        type=Path,
        default=Path("respaldo-apoyos"),
        help="Carpeta de los CSV de respaldo",
    )
    arguments = parser.parse_args()
    sys.exit(
        asyncio.run(
            main(arguments.apply, arguments.backup, arguments.include_requests, arguments.confirm)
        )
    )
