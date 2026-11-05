"""SQLAlchemy models for the shared master catalog."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class MasterDefinitionModel(Base):
    """One master and where its data comes from."""

    __tablename__ = "master_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_system: Mapped[str] = mapped_column(String(20), nullable=False)
    source_object: Mapped[str | None] = mapped_column(String(200))
    grain: Mapped[str | None] = mapped_column(String(200))
    natural_key: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # DWH sequence (``monitoreo.secuencias``) that refreshes the source view (0048).
    dwh_sequence: Mapped[str | None] = mapped_column(String(60))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MasterFieldModel(Base):
    """A declared column of a master."""

    __tablename__ = "master_fields"
    __table_args__ = (UniqueConstraint("master_id", "name", name="uq_master_fields_master_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    master_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("master_definitions.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    data_type: Mapped[str] = mapped_column(String(20), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_nullable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_pii: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    references_slug: Mapped[str | None] = mapped_column(String(80))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)


class MasterRecordModel(Base):
    """A normalized record of a master.

    Unused since the masters are read live from the DWH (0048); the table stays until
    the live reads are validated in production.
    """

    __tablename__ = "master_records"
    __table_args__ = (
        UniqueConstraint("master_id", "source_key", name="uq_master_records_master_source_key"),
        Index("ix_master_records_master_company_code", "master_id", "company_id", "code"),
        # The sync deactivates what a run did not see (migration 0046).
        Index("ix_master_records_master_synced_at", "master_id", "synced_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    master_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("master_definitions.id"), nullable=False
    )
    company_id: Mapped[int | None] = mapped_column(SmallInteger)
    code: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool | None] = mapped_column(Boolean)
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
