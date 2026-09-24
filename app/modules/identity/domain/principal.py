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
    # Holders of the platform ``admin`` role manage every area.
    is_platform_admin: bool


@dataclass(frozen=True)
class DirectoryUser:
    """The fields of a platform user that other modules show."""

    id: UUID
    email: str | None
    display_name: str | None
