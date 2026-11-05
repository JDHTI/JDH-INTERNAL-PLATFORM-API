"""Copy every declared master from the data warehouse into ``master_records``.

Reads each active ``master_definitions`` row's ``source_object`` with the columns of
its ``master_fields`` over the ``DWH_*`` connection (read-only), and upserts by the
master's key field. Runs as a dry run unless ``--apply`` is given. A master that fails
(no permission on its view, say) is reported and the others still sync.

The jobs module runs the same sync when the warehouse sequences finish
(``JOBS_ENABLED``); this script is the manual equivalent.

Usage:
    poetry run python scripts/sync-master-catalog.py
    poetry run python scripts/sync-master-catalog.py --only clientes --only productos
    poetry run python scripts/sync-master-catalog.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.app_container import (
    build_application_container,
    initialize_global_resources,
    shutdown_global_resources,
)
from app.core.config import Settings
from app.core.errors import ApplicationError
from app.infrastructure.data_provider import DataProviderError


async def run(only: list[str], apply: bool) -> int:
    """Run the sync and print one line per master."""

    container = build_application_container(Settings())
    resources = await initialize_global_resources(container)
    try:
        service = container.v1_container.master_catalog_sync()
        reports = await service.run(only or None, apply=apply)
    except (ApplicationError, DataProviderError) as exc:
        print(f"Error: {getattr(exc, 'detail', None) or exc}", file=sys.stderr)
        return 1
    finally:
        await shutdown_global_resources(resources)
    mode = "APLICADO" if apply else "SIMULACIÓN (usa --apply para escribir)"
    print(f"Catálogo de maestros: {mode}")
    for report in reports:
        seconds = (report.finished_at - report.started_at).total_seconds()
        status = f"  ERROR: {report.error}" if report.error else ""
        print(
            f"  {report.slug:<18} leídos={report.read:>7}  cargados={report.upserted:>7}  "
            f"desactivados={report.deactivated:>6}  ({seconds:.1f}s){status}"
        )
    return 1 if any(report.error for report in reports) else 0


def main() -> int:
    """Parse arguments and run."""

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--only", action="append", default=[], help="Sync only this master slug (repeatable)."
    )
    parser.add_argument("--apply", action="store_true", help="Write; default is a dry run.")
    args = parser.parse_args()
    return asyncio.run(run(args.only, args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
