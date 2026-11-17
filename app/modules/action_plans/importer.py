"""Load the 2026 P.04.F.02 matrix (``Base datos``) into the action plans module.

A dry run unless ``--apply``. Idempotent by the code of each action (column E)::

    docker cp "P.04.F.02 Seguimiento PA JDH V2 SIG 2026.xlsx" platform-api:/tmp/p04.xlsx
    docker exec platform-api python -m app.modules.action_plans.importer --file /tmp/p04.xlsx
    docker exec platform-api python -m app.modules.action_plans.importer \
        --file /tmp/p04.xlsx --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.app_container import (
    build_application_container,
    initialize_global_resources,
    shutdown_global_resources,
)
from app.core.config import Settings
from app.modules.action_plans.application.importer import import_matrix, summary


async def _run(path: Path, apply: bool) -> int:
    container = build_application_container(Settings())
    resources = await initialize_global_resources(container)
    try:
        repository = container.v1_container.action_plan_repository()
        report = await import_matrix(path.read_bytes(), repository, apply)
    except ValueError as exc:
        print(f"No se pudo leer el libro: {exc}", file=sys.stderr)
        return 1
    finally:
        await shutdown_global_resources(resources)
    for line in summary(report, apply):
        print(line)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=Path, required=True, help="El libro P.04.F.02 (.xlsx)")
    parser.add_argument("--apply", action="store_true", help="Escribe; sin esto solo simula")
    args = parser.parse_args()
    if not args.file.is_file():
        print(f"No existe el archivo {args.file}", file=sys.stderr)
        return 1
    return asyncio.run(_run(args.file, args.apply))


if __name__ == "__main__":
    sys.exit(main())
