"""Framework-independent identity entities used by application services."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class EntraProfile:
    """Profile claims obtained from Entra ID and Microsoft Graph."""

    tenant_id: str
    object_id: str
    name: str
    email: str | None
    job_title: str | None = None
    photo: bytes | None = None
    photo_content_type: str | None = None


@dataclass(frozen=True, slots=True)
class IdentityToken:
    """Validated token claims needed by the local identity service."""

    tenant_id: str
    object_id: str
    name: str | None
    email: str | None
    access_token: str


@dataclass(frozen=True, slots=True)
class UserView:
    """Stable application representation of a local user."""

    id: UUID
    tenant_id: str
    entra_object_id: str
    name: str
    email: str | None
    job_title: str | None
    is_active: bool
    role: str
    area_id: UUID | None
    area_name: str | None
    photo_available: bool
