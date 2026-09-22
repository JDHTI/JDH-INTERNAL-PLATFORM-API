"""Promote a registered Entra user to the initial platform administrator.

Usage:
    poetry run python scripts/promote-user.py --tenant-id <tenant> --entra-object-id <oid>
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


async def promote(tenant_id: str, object_id: str) -> int:
    """Promote one existing user and return a process status code."""

    app_settings = Settings()
    container = build_application_container(app_settings)
    resources = await initialize_global_resources(container)
    try:
        service = container.v1_container.identity_service()
        user = await service.promote_user(tenant_id, object_id)
        if user is None:
            print("User not found. The user must sign in once before promotion.")
            return 1
        print(f"Promoted {user.email or user.entra_object_id} to admin.")
        return 0
    finally:
        await shutdown_global_resources(resources)


def main() -> None:
    """Parse command arguments and run the promotion use case."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--entra-object-id", required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(promote(args.tenant_id, args.entra_object_id)))


if __name__ == "__main__":
    main()
