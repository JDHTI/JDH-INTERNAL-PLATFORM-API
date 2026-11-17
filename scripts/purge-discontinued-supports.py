"""Delete the supports of the discontinued classes (rules v2, 2026-11-17).

BI decided that ``lista_especial``, ``recuperacion_cliente`` and ``liquidacion_inventario`` are
gone for good: their supports are deleted, not voided. ``rebate`` and ``presupuesto`` stay
(kept apart for a later module).

What it does, all in one transaction:

1. Writes every row (all columns, tiers included) to a CSV backup first.
2. Drops the support's pending outbox events, so a late version cannot reach the DWH after the
   delete, and queues a delete event (version + 1) for ``plataforma.borrar_apoyo_proveedor``.
3. Deletes the supports (tiers, costs and document links go with them).

The JDH history is immutable (trigger ``protect_support_jdh_history``). A support with a JDH
request or decision (``solicitada``, ``aprobada``, ``rechazada``, ``cancelada``) is never
deleted: the script stops and lists them. The events the audit trigger writes by itself on
every insert and edit (``creacion``, ``actualizacion_maestro``, and migration 0036's
``migracion`` baseline) record the master, not a JDH decision; with
``--include-baseline-history`` they are deleted with their support (the backup keeps them),
the trigger switched off only inside that transaction. A dry run unless ``--apply``.

Usage:
    poetry run python scripts/purge-discontinued-supports.py
    poetry run python scripts/purge-discontinued-supports.py --apply --include-baseline-history
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select, text  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.infrastructure.database import Database  # noqa: E402
from app.modules.dwh_publish.domain.entities import PUBLISHED  # noqa: E402
from app.modules.dwh_publish.infrastructure.models import DwhOutboxModel  # noqa: E402
from app.modules.dwh_publish.infrastructure.outbox import enqueue  # noqa: E402
from app.modules.supplier_supports.domain.vocabulary import DISCONTINUED_KINDS  # noqa: E402
from app.modules.supplier_supports.infrastructure.models import (  # noqa: E402
    SupplierSupportModel,
    SupplierSupportTierModel,
)
from app.modules.supplier_supports.infrastructure.publication import (  # noqa: E402
    SUPPORT_TOPIC,
    _record,
    delete_event,
)

S = SupplierSupportModel
ACTOR = "purga-clases-descontinuadas"
# JDH events the audit trigger writes on every insert or edit of a support (and the
# migration's baseline): a record of the master, not a JDH decision. The decisions are the
# requests and their events (solicitada, aprobada, rechazada, cancelada).
_BASELINE = "e.event_type IN ('migracion', 'creacion', 'actualizacion_maestro')"


async def main(apply: bool, backup: Path, baseline: bool) -> int:
    database = Database(Settings().DATABASE_URL.get_secret_value())
    try:
        async with database.session_factory() as session:
            counts = (
                await session.execute(
                    select(S.kind, S.status, func.count())
                    .where(S.kind.in_(DISCONTINUED_KINDS))
                    .group_by(S.kind, S.status)
                    .order_by(S.kind, S.status)
                )
            ).all()
            total = sum(count for _, _, count in counts)
            print(f"Apoyos de clases descontinuadas: {total}")
            for kind, status, count in counts:
                print(f"  {kind:<24} {status:<20} {count}")
            if not total:
                return 0
            kinds = {"kinds": list(DISCONTINUED_KINDS)}
            requests = await session.execute(
                text(
                    "SELECT DISTINCT r.support_id FROM supplier_support_requests r "
                    "JOIN supplier_supports s ON s.id = r.support_id WHERE s.kind = ANY(:kinds)"
                ),
                kinds,
            )
            decisions = await session.execute(
                text(
                    "SELECT DISTINCT e.support_id FROM supplier_support_jdh_events e "
                    "JOIN supplier_supports s ON s.id = e.support_id "
                    f"WHERE s.kind = ANY(:kinds) AND NOT ({_BASELINE})"
                ),
                kinds,
            )
            blocked = [row[0] for row in (*requests.all(), *decisions.all())]
            baseline_events = await session.scalar(
                text(
                    "SELECT count(*) FROM supplier_support_jdh_events e "
                    "JOIN supplier_supports s ON s.id = e.support_id "
                    f"WHERE s.kind = ANY(:kinds) AND ({_BASELINE})"
                ),
                kinds,
            )
            print(f"Eventos JDH de línea base (sistema): {baseline_events}")
            if blocked:
                print("No se borran: tienen solicitudes o decisiones JDH. Anúlalos en su lugar:")
                for support_id in sorted(set(blocked), key=str):
                    print(f"  {support_id}")
                return 1
            if baseline_events and not baseline:
                print(
                    "Tienen eventos JDH de línea base: agrega --include-baseline-history para "
                    "borrarlos con su apoyo (quedan en el respaldo)."
                )
                return 1
            if not apply:
                print("Simulación: nada se borró. Usa --apply para borrar (con respaldo).")
                return 0

            models = list(
                (await session.execute(select(S).where(S.kind.in_(DISCONTINUED_KINDS)))).scalars()
            )
            ids = [model.id for model in models]
            tiers: dict[object, list[dict]] = {}
            for tier in (
                await session.execute(
                    select(SupplierSupportTierModel).where(
                        SupplierSupportTierModel.support_id.in_(ids)
                    )
                )
            ).scalars():
                tiers.setdefault(tier.support_id, []).append(
                    {
                        "threshold_type": tier.threshold_type,
                        "from_value": str(tier.from_value),
                        "to_value": None if tier.to_value is None else str(tier.to_value),
                        "value": str(tier.value),
                    }
                )
            backup.parent.mkdir(parents=True, exist_ok=True)
            history: dict[object, list[dict]] = {}
            for row in (
                await session.execute(
                    text(
                        "SELECT e.support_id, e.event_type, e.actor, e.created_at, e.reason, "
                        "e.before, e.after FROM supplier_support_jdh_events e "
                        "WHERE e.support_id = ANY(:ids)"
                    ),
                    {"ids": ids},
                )
            ).all():
                history.setdefault(row.support_id, []).append(
                    {
                        "event_type": row.event_type,
                        "actor": row.actor,
                        "created_at": row.created_at.isoformat(),
                        "reason": row.reason,
                        "before": row.before,
                        "after": row.after,
                    }
                )
            with backup.open("w", newline="", encoding="utf-8-sig") as handle:
                columns = [column.name for column in S.__table__.columns]
                writer = csv.DictWriter(handle, fieldnames=[*columns, "tiers", "jdh_events"])
                writer.writeheader()
                for model in models:
                    record = _record(model)
                    row = {
                        name: json.dumps(value) if isinstance(value, list | dict) else value
                        for name, value in record.items()
                    }
                    writer.writerow(
                        {
                            **row,
                            "tiers": json.dumps(tiers.get(model.id, [])),
                            "jdh_events": json.dumps(history.get(model.id, []), default=str),
                        }
                    )
            print(f"Respaldo: {backup.resolve()} ({len(models)} filas)")

            now = datetime.now(UTC)
            # The reads above opened the transaction: the writes join it and commit together.
            dropped = await session.execute(
                delete(DwhOutboxModel).where(
                    DwhOutboxModel.topic == SUPPORT_TOPIC,
                    DwhOutboxModel.entity_id.in_(ids),
                    DwhOutboxModel.status != PUBLISHED,
                )
            )
            await enqueue(
                session,
                [delete_event(model.id, model.version + 1, ACTOR, now) for model in models],
            )
            if history:
                # Only the baseline events (checked above); the trigger is off for this
                # transaction alone (ALTER TABLE is transactional and locks the table).
                await session.execute(
                    text(
                        "ALTER TABLE supplier_support_jdh_events "
                        "DISABLE TRIGGER protect_support_jdh_history"
                    )
                )
                await session.execute(
                    text("DELETE FROM supplier_support_jdh_events WHERE support_id = ANY(:ids)"),
                    {"ids": list(history)},
                )
                await session.execute(
                    text(
                        "ALTER TABLE supplier_support_jdh_events "
                        "ENABLE TRIGGER protect_support_jdh_history"
                    )
                )
            await session.execute(delete(S).where(S.id.in_(ids)))
            await session.commit()
            print(
                f"Borrados {len(ids)} apoyos; {dropped.rowcount} eventos pendientes descartados; "
                f"{len(ids)} borrados en cola para el DWH."
            )
            return 0
    finally:
        await database.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--apply", action="store_true", help="Borrar (por defecto, simula)")
    parser.add_argument(
        "--backup",
        type=Path,
        default=Path(f"purga-apoyos-{datetime.now(UTC):%Y%m%d-%H%M%S}.csv"),
        help="CSV de respaldo antes de borrar",
    )
    parser.add_argument(
        "--include-baseline-history",
        action="store_true",
        help="Borrar también los eventos JDH de línea base del sistema (quedan en el respaldo)",
    )
    arguments = parser.parse_args()
    sys.exit(
        asyncio.run(main(arguments.apply, arguments.backup, arguments.include_baseline_history))
    )
