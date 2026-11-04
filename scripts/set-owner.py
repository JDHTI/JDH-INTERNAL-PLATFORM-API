"""Make a user the single platform owner; the previous owner becomes admin.

The owner bypasses every check and is the only one who grants or revokes the global
roles ``admin`` and ``moderator``. The API never assigns it: this script does, after the
person signed in once or came in with the directory sync.

Usage:
    poetry run python scripts/set-owner.py --email analista.ti@jdh.com.co
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


async def set_owner(email: str) -> int:
    """Transfer the owner role and return a process status code."""

    container = build_application_container(Settings())
    resources = await initialize_global_resources(container)
    try:
        service = container.v1_container.identity_service()
        await service.list_roles()  # creates the owner role on a database without 0041
        user = await service.set_owner(email)
        if user is None:
            print(f"No active user with {email}: sign in or sync the directory first.")
            return 1
        await service.audit.record_global_event(
            "global_role_changed", None, target_user_id=user.id, detail={"after": "owner"}
        )
        print(f"{user.email} is now the platform owner.")
        return 0
    finally:
        await shutdown_global_resources(resources)


def main() -> None:
    """Parse the arguments and run."""

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--email", default=Settings().PLATFORM_OWNER_EMAIL)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(set_owner(args.email)))


if __name__ == "__main__":
    main()
