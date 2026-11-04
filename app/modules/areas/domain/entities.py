"""Areas of the organization, their role catalog and their members."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.errors import InvalidInputError

# The slug is the area's URL segment in the UI (/ti, /calidad): lowercase,
# ASCII, hyphen-separated, and immutable once created.
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass
class Area:
    """A department with its own space, role catalog and document space."""

    id: UUID
    slug: str
    name: str
    remit: str | None
    icon: str | None
    sort_order: int
    responsible_user_id: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass
class AreaRole:
    """A role that exists only inside one area, e.g. "Líder BI"."""

    id: UUID
    area_id: UUID
    name: str
    created_at: datetime
    updated_at: datetime


@dataclass
class AreaMember:
    """A user assigned to an area with one of that area's roles."""

    id: UUID
    area_id: UUID
    user_id: UUID
    role_id: UUID
    created_at: datetime
    updated_at: datetime


def clean_name(value: str, field: str = "nombre", max_length: int = 150) -> str:
    """Collapse whitespace and reject blank or oversized names."""

    cleaned = " ".join(value.split())
    if not cleaned:
        raise InvalidInputError(f"El {field} no puede estar vacío")
    if len(cleaned) > max_length:
        raise InvalidInputError(f"El {field} admite máximo {max_length} caracteres")
    return cleaned


# Top-level routes of the UI shared by everyone signed in. An area is served at /{slug}
# and a fixed route wins over it, so an area named like one of them would be unreachable.
RESERVED_SLUGS = frozenset(
    {
        "administracion",
        "configuracion",
        "directorio",
        "documentos",
        "eventos",
        "inicio",
        "login",
        "noticias",
        "perfil",
        "pqrsf",
        "sugerencias",
    }
)


def validate_slug(slug: str) -> str:
    """Return the slug when it is a valid URL segment not taken by a general route."""

    if not SLUG_PATTERN.fullmatch(slug) or len(slug) > 50:
        raise InvalidInputError(
            "El identificador debe ir en minúsculas, sin tildes ni espacios (ej. talento-humano)"
        )
    if slug in RESERVED_SLUGS:
        raise InvalidInputError(
            f"El identificador {slug} está reservado para una sección general de la plataforma"
        )
    return slug
