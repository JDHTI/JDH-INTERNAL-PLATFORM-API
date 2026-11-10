"""SQLAlchemy model of the DWH outbox."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class DwhOutboxModel(Base):
    """One version of a record to publish in the warehouse.

    Written in the transaction of the change; kept after it is published, so it is also the
    local history of every version (the platform's own backup of what reached the DWH).
    """

    __tablename__ = "dwh_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pendiente', 'publicado', 'fallido')", name="ck_dwh_outbox_status"
        ),
        UniqueConstraint("topic", "entity_id", "version", name="uq_dwh_outbox_version"),
        Index(
            "ix_dwh_outbox_due",
            "next_attempt_at",
            "id",
            postgresql_where=text("status = 'pendiente'"),
        ),
        Index("ix_dwh_outbox_entity", "topic", "entity_id", "version"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    topic: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="pendiente")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
