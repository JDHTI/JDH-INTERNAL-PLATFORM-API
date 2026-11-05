"""Areas of the organization, their role catalog and their members."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.errors import InvalidInputError
from app.modules.areas.domain.catalog import (
    MANAGE,
    READ,
    WILDCARD,
    areas_for,
    implies,
    modules_for,
)
from app.modules.identity.domain.roles import MODERATOR, is_platform_admin

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
    """A role that exists only inside one area, e.g. "Analista BI".

    Each area has exactly one leader role: it holds every permission of the area and
    can be renamed, but never deleted nor have its permissions changed.
    """

    id: UUID
    area_id: UUID
    name: str
    created_at: datetime
    updated_at: datetime
    is_leader: bool = False


@dataclass
class AreaMember:
    """A user assigned to an area with one of that area's roles."""

    id: UUID
    area_id: UUID
    user_id: UUID
    role_id: UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class RolePermission:
    """One grant of an area role: a module (or ``*``) and an action."""

    module: str
    action: str


@dataclass(frozen=True)
class PermissionEvent:
    """One change to who may do what, for the audit log."""

    id: UUID
    at: datetime
    action: str
    actor_user_id: UUID | None
    area_id: UUID | None
    target_user_id: UUID | None
    role_id: UUID | None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AreaGrants:
    """What a person may do in one area: as its responsible, its leader, or by their role."""

    area_id: UUID
    slug: str
    is_responsible: bool = False
    role_id: UUID | None = None
    # Module key (or ``*``) -> ``read`` | ``manage``.
    grants: dict[str, str] = field(default_factory=dict)
    # The person holds the area's leader role: everything, whatever the stored grants say.
    is_leader: bool = False

    def action_for(self, module: str) -> str | None:
        """Strongest action held on a module (``manage`` > ``read`` > ``None``)."""

        if self.is_responsible or self.is_leader:
            return MANAGE
        held = {self.grants.get(WILDCARD), self.grants.get(module)}
        if MANAGE in held:
            return MANAGE
        return READ if READ in held else None


@dataclass(frozen=True)
class AccessProfile:
    """Everything a person may do in the workspace."""

    role: str
    areas: dict[str, AreaGrants] = field(default_factory=dict)

    def allows(self, area_slug: str | None, module: str, action: str = READ) -> bool:
        """Whether the person may do ``action`` on ``module``.

        Args:
            area_slug (str | None): The area; ``None`` asks for any area of the module.
            module (str): Module key.
            action (str): ``read`` or ``manage``.

        Returns:
            bool: Owner and admin always; moderator only to read; otherwise the
                area responsible or a role that covers the module.
        """

        if is_platform_admin(self.role):
            return True
        if self.role == MODERATOR:
            return action == READ
        if area_slug is not None:
            grants = self.areas.get(area_slug)
            return grants is not None and implies(grants.action_for(module), action)
        slugs = areas_for(module)
        return any(
            implies(grants.action_for(module), action)
            for slug, grants in self.areas.items()
            if slugs is None or slug in slugs
        )

    def modules(self, area_slug: str) -> dict[str, str]:
        """The module -> action map of an area, with the wildcard expanded.

        Args:
            area_slug (str): Area slug.

        Returns:
            dict[str, str]: Each module the person may at least read.
        """

        everything = (
            MANAGE if is_platform_admin(self.role) else READ if self.role == MODERATOR else None
        )
        grants = self.areas.get(area_slug)
        result: dict[str, str] = {}
        for module in modules_for(area_slug):
            action = everything or (grants.action_for(module.key) if grants else None)
            if action is not None:
                result[module.key] = action
        return result


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
