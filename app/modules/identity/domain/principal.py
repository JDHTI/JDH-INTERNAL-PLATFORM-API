"""The authenticated caller, as other modules see it."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Principal:
    """Who is performing the current request.

    Other modules (areas, documents) depend on this value instead of the
    identity persistence model, so they never read the users table.
    """

    user_id: UUID
    # Email when Entra provides one, otherwise the display name: used to
    # attribute folders and documents to a person.
    email: str
    display_name: str | None
    # Holders of the platform ``owner`` or ``admin`` role manage every area.
    is_platform_admin: bool
    # Global role code: owner, admin, moderator or user.
    role: str = "user"

    @property
    def is_owner(self) -> bool:
        """Whether the caller is the platform owner."""

        return self.role == "owner"

    @property
    def is_moderator(self) -> bool:
        """Whether the caller reads everything but writes nothing."""

        return self.role == "moderator"


@dataclass(frozen=True)
class DirectoryUser:
    """The fields of a platform user that other modules show."""

    id: UUID
    email: str | None
    display_name: str | None
    is_active: bool = True
