"""Commands and results of the area use cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class UserRef:
    id: UUID
    email: str | None
    display_name: str | None


@dataclass(frozen=True)
class RoleRef:
    id: UUID
    name: str
    is_leader: bool = False


@dataclass(frozen=True)
class AreaView:
    """An area as seen by one principal."""

    id: UUID
    slug: str
    name: str
    remit: str | None
    icon: str | None
    sort_order: int
    responsible: UserRef | None
    my_role: RoleRef | None
    is_responsible: bool
    created_at: datetime
    updated_at: datetime
    # Module key -> ``read`` | ``manage`` the caller holds in this area (wildcard expanded).
    my_modules: dict[str, str] = field(default_factory=dict)
    # The general masters its ``maestros`` lists, in menu order; empty when the module is off.
    masters: tuple[str, ...] = ()


@dataclass(frozen=True)
class AreaGrantsView:
    """One area of a person, with their role there and its grants."""

    area_id: UUID
    slug: str
    name: str
    is_responsible: bool
    role: RoleRef | None
    grants: list = field(default_factory=list)


@dataclass(frozen=True)
class ModuleStateView:
    """Whether a module is on in an area, with its settings."""

    is_enabled: bool
    config: dict[str, Any] = field(default_factory=dict)
    # No stored choice: the catalog default applies.
    is_default: bool = True


@dataclass(frozen=True)
class AreaModulesView:
    """An area with the state of every module of the catalog."""

    area_id: UUID
    slug: str
    name: str
    modules: dict[str, ModuleStateView] = field(default_factory=dict)


@dataclass(frozen=True)
class MemberView:
    id: UUID
    user: UserRef
    role: RoleRef
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CreateAreaCommand:
    slug: str
    name: str
    remit: str | None = None
    icon: str | None = None
    sort_order: int = 0
    responsible_email: str | None = None


@dataclass(frozen=True)
class UpdateAreaCommand:
    """Partial update: only the names listed in ``fields`` are applied."""

    fields: frozenset[str] = field(default_factory=frozenset)
    name: str | None = None
    remit: str | None = None
    icon: str | None = None
    sort_order: int | None = None
    responsible_email: str | None = None
