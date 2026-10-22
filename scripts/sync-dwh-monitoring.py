"""Run one load cycle of the data warehouse monitoring copy by hand.

Reads the ``monitoreo`` views over the ``DWH_*`` connection (the one of the masters)
and writes them to the platform's ``dwh_mon_*`` tables, exactly as the background loop
does. Useful for the first load, which takes 90 days of history, and to check the
connection.

Usage:
    poetry run python scripts/sync-dwh-monitoring.py
    poetry run python scripts/sync-dwh-monitoring.py --only secuencias --only frescura_resumen
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
from app.modules.dwh_monitoring.domain.entities import CycleStatus
from app.modules.dwh_monitoring.domain.views import VIEWS


async def run(only: list[str]) -> int:
    """Run a cycle and print one line per view."""

    container = build_application_container(Settings())
    resources = await initialize_global_resources(container)
    try:
        cycle = await container.v1_container.dwh_monitoring_sync_service().run(only or None)
    finally:
        await shutdown_global_resources(resources)
    seconds = (cycle.finished_at - cycle.started_at).total_seconds() if cycle.finished_at else 0
    print(f"Carga {cycle.status} en {seconds:.1f}s")
    for name, report in cycle.views.items():
        error = f"  ERROR: {report.error}" if report.error else ""
        print(f"  {name:<28} leídas={report.read:>7}  escritas={report.written:>7}{error}")
    if cycle.error:
        print(f"Error: {cycle.error}", file=sys.stderr)
    return 0 if cycle.status is CycleStatus.SUCCESS else 1


def main() -> int:
    """Parse arguments and run."""

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        choices=[view.name for view in VIEWS],
        help="Load only this view (repeatable). Default: all.",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.only))


if __name__ == "__main__":
    raise SystemExit(main())
