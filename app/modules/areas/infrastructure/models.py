"""SQLAlchemy models owned by the areas module."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base

# The ``areas`` row is shared with the identity module (users.area_id points
# at it), so this module maps the identity model rather than a second class
# on the same table. Its area-space columns come from migration 0003_areas.
from app.modules.identity.infrastructure.models import AreaModel

__all__ = [
    "AreaMemberModel",
    "AreaModel",
    "AreaModuleModel",
    "AreaRoleModel",
    "AreaRolePermissionModel",
    "PermissionEventModel",
]


class AreaRoleModel(Base):
    __tablename__ = "area_roles"
    __table_args__ = (
        # Target of the composite FK that keeps a member's role in their area.
        UniqueConstraint("area_id", "id", name="uq_area_roles_area_id_id"),
        Index("uq_area_roles_area_name_lower", "area_id", text("lower(name)"), unique=True),
        # One leader role per area: all its permissions, never deleted or narrowed.
        Index(
            "uq_area_roles_one_leader",
            "area_id",
            unique=True,
            postgresql_where=text("is_leader"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    area_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("areas.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_leader: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AreaMemberModel(Base):
    __tablename__ = "area_members"
    __table_args__ = (
        UniqueConstraint("area_id", "user_id", name="uq_area_members_area_user"),
        # NO ACTION (not RESTRICT): checked at statement end, so deleting the
        # area cascades through roles and members without tripping it.
        ForeignKeyConstraint(
            ["area_id", "role_id"],
            ["area_roles.area_id", "area_roles.id"],
            name="fk_area_members_role_same_area",
        ),
        Index("ix_area_members_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    area_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("areas.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AreaRolePermissionModel(Base):
    """One grant of an area role: a module (or ``*``) and ``read`` or ``manage``."""

    __tablename__ = "area_role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "module", name="uq_area_role_permissions_role_module"),
        CheckConstraint("action IN ('read', 'manage')", name="ck_area_role_permissions_action"),
        Index("ix_area_role_permissions_role_id", "role_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("area_roles.id", ondelete="CASCADE"), nullable=False
    )
    module: Mapped[str] = mapped_column(String(60), nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AreaModuleModel(Base):
    """An administrator's choice for one module in one area.

    No row means the catalog default (``catalog.MODULES[].area_slugs``).
    """

    __tablename__ = "area_modules"
    __table_args__ = (UniqueConstraint("area_id", "module", name="uq_area_modules_area_module"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    area_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("areas.id", ondelete="CASCADE"), nullable=False
    )
    module: Mapped[str] = mapped_column(String(60), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PermissionEventModel(Base):
    """One change to who may do what (roles, grants, members, global roles)."""

    __tablename__ = "permission_events"
    __table_args__ = (
        Index("ix_permission_events_at", "at"),
        Index("ix_permission_events_target_user_id", "target_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    area_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("areas.id", ondelete="SET NULL")
    )
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # No foreign key: the history outlives a deleted role.
    role_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
