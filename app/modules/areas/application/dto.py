"""Commands and results of the area use cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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
