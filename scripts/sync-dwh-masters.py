"""Copy the commercial masters (suppliers, customers, products) from the data warehouse.

Reads the ``consumo`` views over the configured ``DWH_*`` connection (read-only)
and upserts them by warehouse key into the platform's own tables. Runs as a dry
run unless ``--apply`` is given; a rerun updates rows in place and deactivates
those that left the warehouse.

Masters, in dependency order: ``commercial_suppliers`` (ERP supplier codes and
their probable NITs), ``suppliers``, ``customers``, ``products`` and
``product_suppliers`` (purchase-history bridge).

Usage:
    poetry run python scripts/sync-dwh-masters.py
    poetry run python scripts/sync-dwh-masters.py --only products --only customers
    DWH_COMMAND_TIMEOUT_SECONDS=120 poetry run python scripts/sync-dwh-masters.py --apply
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
from app.modules.commercial_masters.application.sync import MASTERS


async def run(only: list[str], apply: bool) -> int:
    """Run the sync and print one line per master."""

    container = build_application_container(Settings())
    resources = await initialize_global_resources(container)
    try:
        service = container.v1_container.master_sync_service()
        reports = await service.run(only or None, apply=apply)
    except (ApplicationError, DataProviderError) as exc:
        print(f"Error: {getattr(exc, 'detail', None) or exc}", file=sys.stderr)
        return 1
    finally:
        await shutdown_global_resources(resources)
    mode = "APLICADO" if apply else "SIMULACIÓN (usa --apply para escribir)"
    print(f"Sincronización {mode}")
    for report in reports:
        seconds = (report.finished_at - report.started_at).total_seconds()
        extra = "".join(f"  {name}={count}" for name, count in report.extra.items())
        print(
            f"  {report.master:<22} leídos={report.read:>7}  cargados={report.upserted:>7}  "
            f"desactivados={report.deactivated:>6}{extra}  ({seconds:.1f}s)"
        )
    return 0


def main() -> int:
    """Parse arguments and run."""

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        choices=MASTERS,
        help="Sync only this master (repeatable). Default: all.",
    )
    parser.add_argument("--apply", action="store_true", help="Write; default is a dry run.")
    args = parser.parse_args()
    return asyncio.run(run(args.only, args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
