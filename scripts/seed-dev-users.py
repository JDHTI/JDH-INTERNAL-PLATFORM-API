"""Seed deterministic local users for development.

This command is deliberately restricted to ``ENVIRONMENT=dev``. It can be run
repeatedly: users are keyed by their tenant/object identity and are refreshed
instead of duplicated.

Usage:
    poetry run python scripts/seed-dev-users.py
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
from app.core.config import Environment, Settings


async def seed() -> int:
    """Run the development-only seed and print the deterministic identities."""

    app_settings = Settings()
    if app_settings.ENVIRONMENT is not Environment.DEV:
        raise RuntimeError("seed-dev-users.py is only allowed in ENVIRONMENT=dev")

    container = build_application_container(app_settings)
    resources = await initialize_global_resources(container)
    try:
        service = container.v1_container.identity_service()
        users = await service.seed_development_users()
        for user in users:
            print(f"{user.email}: {user.role.code} active={user.is_active}")
        return 0
    finally:
        await shutdown_global_resources(resources)


def main() -> None:
    """Parse command-line options and execute the seed."""

    argparse.ArgumentParser(description=__doc__).parse_args()
    raise SystemExit(asyncio.run(seed()))


if __name__ == "__main__":
    main()
