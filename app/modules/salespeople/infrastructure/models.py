"""Table of the salespeople module: who holds each SIESA salesperson or supervisor code."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base

__all__ = ["SalespersonLinkModel"]


class SalespersonLinkModel(Base):
    __tablename__ = "salesperson_links"
    __table_args__ = (
        # A code belongs to one person at most; a person may hold several codes.
        UniqueConstraint("kind", "code", name="uq_salesperson_links_kind_code"),
        CheckConstraint("kind IN ('seller', 'supervisor')", name="ck_salesperson_links_kind"),
        CheckConstraint(
            "source IN ('auto', 'manual', 'excluded')", name="ck_salesperson_links_source"
        ),
        # An excluded code has nobody; every other link has a person.
        CheckConstraint(
            "(source = 'excluded') = (user_id IS NULL)", name="ck_salesperson_links_user"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
