"""Global roles of the platform, from most to least powerful.

- ``owner``: exactly one person. Everything, and the only one who grants or revokes
  ``admin`` and ``moderator``. Cannot be deactivated or demoted.
- ``admin``: everything in every area, and the permissions panel.
- ``moderator``: reads everything in every area and the permissions log; never writes.
- ``user``: the default; only what the area roles grant.
"""

from __future__ import annotations

from uuid import UUID

OWNER = "owner"
ADMIN = "admin"
MODERATOR = "moderator"
USER = "user"

GLOBAL_ROLES = (OWNER, ADMIN, MODERATOR, USER)
ROLE_NAMES = {
    OWNER: "Propietario",
    ADMIN: "Administrador",
    MODERATOR: "Moderador",
    USER: "Usuario",
}
# Roles that bypass every area check and may write anywhere.
PLATFORM_ADMIN_ROLES = frozenset({OWNER, ADMIN})
# Roles the owner can grant through the API (the owner moves with a script).
ASSIGNABLE_ROLES = (ADMIN, MODERATOR, USER)

# Fixed identifiers: admin and user come from migration 0002, owner and moderator from 0041.
ADMIN_ROLE_ID = UUID("8b2f5c0d-311f-49fa-9c84-7b9d8be55d1a")
USER_ROLE_ID = UUID("f15e1f8f-2b76-4c1d-86f7-54c9d3b8ae7e")
OWNER_ROLE_ID = UUID("5d0b6a52-7f8e-4c63-9d1a-0b3f4e2c8a71")
MODERATOR_ROLE_ID = UUID("c47e9f1d-28a3-4b5e-8f60-6d2a91b3e4c5")


def is_platform_admin(role_code: str | None) -> bool:
    """Whether a global role bypasses every area check.

    Args:
        role_code (str | None): Code of the global role.

    Returns:
        bool: ``True`` for owner and admin.
    """

    return role_code in PLATFORM_ADMIN_ROLES
