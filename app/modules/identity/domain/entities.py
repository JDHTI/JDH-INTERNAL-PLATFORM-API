"""Identity entities: platform users and the authenticated principal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass
class User:
    """A person known to the platform.

    Users are provisioned on their first authenticated request, or earlier as
    a stub (email only) when an administrator adds them to an area before they
    ever sign in. ``entra_object_id`` links the stub once they do.
    """

    id: UUID
    email: str
    display_name: str | None
    entra_object_id: str | None
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class VerifiedIdentity:
    """Claims taken from a verified credential, before touching persistence."""

    email: str
    display_name: str | None = None
    entra_object_id: str | None = None


@dataclass(frozen=True)
class Principal:
    """Who is performing the current request."""

    user_id: UUID
    email: str
    display_name: str | None
    is_platform_admin: bool


def normalize_email(email: str) -> str:
    return email.strip().lower()
