"""SQLAlchemy models owned by the identity module."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base


class RoleModel(Base):
    """A platform permission role such as ``admin`` or ``user``."""

    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    users: Mapped[list[UserModel]] = relationship(back_populates="role")


class AreaModel(Base):
    """An organization area to which users can be assigned.

    The same row is the area's space in the areas module: ``slug``, ``remit``,
    ``icon``, ``sort_order`` and ``responsible_user_id`` belong to that module
    (migration ``0003_areas``), which maps this model instead of a second one
    so both modules read and write one table.
    """

    __tablename__ = "areas"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_areas_tenant_name"),
        Index("ix_areas_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    # URL segment of the area in the UI (/ti, /calidad). Immutable once set.
    slug: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    remit: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # lucide-react icon name the UI maps to a glyph, e.g. "Server".
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(
        # use_alter: users.area_id already points here, so the two tables
        # reference each other and one FK has to be added after both exist.
        ForeignKey("users.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Explicit foreign keys: areas and users are joined both by users.area_id
    # and by areas.responsible_user_id, so the join path must be named.
    users: Mapped[list[UserModel]] = relationship(
        back_populates="area", foreign_keys="UserModel.area_id"
    )


class UserModel(Base):
    """A local user linked to one Entra object in the configured tenant."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "entra_object_id", name="uq_users_tenant_entra_object"),
        Index("ix_users_tenant_id", "tenant_id"),
        Index("ix_users_email", "email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False)
    entra_object_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id"), nullable=False)
    area_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("areas.id", ondelete="SET NULL"), nullable=True
    )
    photo_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    photo_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    role: Mapped[RoleModel] = relationship(back_populates="users")
    area: Mapped[AreaModel | None] = relationship(back_populates="users", foreign_keys=[area_id])
