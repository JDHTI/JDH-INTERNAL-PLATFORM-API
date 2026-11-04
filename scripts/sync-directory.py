"""Run one directory sync with Microsoft Graph by hand.

Reads every Entra user with the ``DIRECTORY_GRAPH_*`` credentials, upserts the employees
and their platform users, deactivates the leavers and downloads the changed photos,
exactly as the background loop does. Useful for the first load and to check the
credentials; it works with ``DIRECTORY_SYNC_ENABLED=false``.

Usage:
    poetry run python scripts/sync-directory.py
    poetry run python scripts/sync-directory.py --recover
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
from app.core.errors import ConflictError
from app.modules.directory.domain.entities import RunStatus, RunTrigger


async def run(recover: bool) -> int:
    """Run a sync and print its counts."""

    settings = Settings()
    if not settings.directory_graph_configured:
        print(
            "Faltan DIRECTORY_GRAPH_CLIENT_ID, DIRECTORY_GRAPH_CLIENT_SECRET o el tenant",
            file=sys.stderr,
        )
        return 2
    container = build_application_container(settings)
    resources = await initialize_global_resources(container)
    v1 = container.v1_container
    try:
        service = v1.directory_sync_service()
        if recover:
            print(f"Corridas cerradas: {await service.recover_stale_runs()}")
        try:
            result = await service.run(RunTrigger.MANUAL, "script")
        except ConflictError as exc:
            print(f"{exc.detail}. Usa --recover si el proceso anterior se detuvo.", file=sys.stderr)
            return 1
    finally:
        await v1.directory_graph_client().close()
        await shutdown_global_resources(resources)
    seconds = (result.finished_at - result.started_at).total_seconds() if result.finished_at else 0
    print(f"Sincronización {result.status} en {seconds:.1f}s")
    for name, value in result.counts.items():
        print(f"  {name:<22} {value:>6}")
    if result.error_message:
        print(f"Error: {result.error_code}: {result.error_message}", file=sys.stderr)
    return 0 if result.status is RunStatus.SUCCESS else 1


def main() -> int:
    """Parse arguments and run."""

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--recover",
        action="store_true",
        help="Close as failed the syncs a stopped process left running.",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.recover))


if __name__ == "__main__":
    raise SystemExit(main())
