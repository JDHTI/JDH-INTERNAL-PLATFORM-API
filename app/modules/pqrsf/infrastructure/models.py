"""SQLAlchemy models for PQRSF types, requests and their follow-up cases."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class RequestTypeModel(Base):
    """Fixed PQRSF request type catalog."""

    __tablename__ = "pqrsf_request_types"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    allows_reason: Mapped[bool] = mapped_column(nullable=False)
    allows_attachments: Mapped[bool] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    prefix: Mapped[str] = mapped_column(String(5), nullable=False, unique=True)
    requires_follow_up: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    sla_days: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PqrsfRequestModel(Base):
    """Customer-submitted PQRSF request."""

    __tablename__ = "pqrsf_requests"
    __table_args__ = (
        UniqueConstraint("reference", name="pqrsf_requests_reference_key"),
        UniqueConstraint("idempotency_key", name="pqrsf_requests_idempotency_key_key"),
        Index("ix_pqrsf_requests_type_created", "request_type_id", "created_at"),
        Index("ix_pqrsf_requests_site_created", "site_id", "created_at"),
        Index("ix_pqrsf_requests_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    reference: Mapped[str] = mapped_column(String(50), nullable=False)
    request_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pqrsf_request_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_sites.id", ondelete="RESTRICT"), nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    business_name: Mapped[str] = mapped_column(String(200), nullable=False)
    nit: Mapped[str] = mapped_column(String(30), nullable=False)
    address: Mapped[str] = mapped_column(String(300), nullable=False)
    department: Mapped[str] = mapped_column(String(100), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PqrsfAttachmentModel(Base):
    """Metadata for one sanitized PQRSF image."""

    __tablename__ = "pqrsf_attachments"
    __table_args__ = (
        UniqueConstraint("storage_key", name="pqrsf_attachments_storage_key_key"),
        Index("ix_pqrsf_attachments_request_id", "request_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_requests.id", ondelete="CASCADE"), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    extension: Mapped[str] = mapped_column(String(8), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CaseStatusModel(Base):
    """Configurable follow-up status."""

    __tablename__ = "pqrsf_case_statuses"
    __table_args__ = (
        Index(
            "uq_pqrsf_case_statuses_single_initial",
            "is_initial",
            unique=True,
            postgresql_where=text("is_initial"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    color: Mapped[str | None] = mapped_column(String(7))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_initial: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_terminal: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DependencyModel(Base):
    """Internal scope in charge of a case."""

    __tablename__ = "pqrsf_dependencies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DocumentTypeModel(Base):
    """Document a case can request, with its agreed response time."""

    __tablename__ = "pqrsf_document_types"
    __table_args__ = (
        Index("uq_pqrsf_document_types_name", func.lower(text("name")), unique=True),
        CheckConstraint("sla_days >= 0", name="ck_pqrsf_document_types_sla_days"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sla_days: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CaseSequenceModel(Base):
    """Last consecutive used per request type and year."""

    __tablename__ = "pqrsf_case_sequences"
    __table_args__ = (
        PrimaryKeyConstraint("request_type_id", "year", name="pk_pqrsf_case_sequences"),
    )

    request_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pqrsf_request_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False)


class CaseModel(Base):
    """Follow-up of one PQRSF."""

    __tablename__ = "pqrsf_cases"
    __table_args__ = (
        UniqueConstraint("code", name="pqrsf_cases_code_key"),
        UniqueConstraint("request_id", name="pqrsf_cases_request_id_key"),
        UniqueConstraint(
            "request_type_id", "year", "number", name="pqrsf_cases_type_year_number_key"
        ),
        CheckConstraint(
            "origin IN ('public_form', 'internal', 'import')", name="ck_pqrsf_cases_origin"
        ),
        Index("ix_pqrsf_cases_status", "status_id"),
        Index("ix_pqrsf_cases_assignee_status", "assignee_user_id", "status_id"),
        Index("ix_pqrsf_cases_type_received", "request_type_id", "received_on"),
        Index("ix_pqrsf_cases_received_on", "received_on"),
        Index("ix_pqrsf_cases_due_on", "due_on"),
        Index("ix_pqrsf_cases_created_by", "created_by_user_id"),
        Index(
            "ix_pqrsf_cases_legacy_source_key",
            text("(legacy ->> 'source_key')"),
            postgresql_where=text("legacy IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    request_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pqrsf_request_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pqrsf_case_statuses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    due_on: Mapped[date | None] = mapped_column(Date)
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_sites.id", ondelete="RESTRICT")
    )
    dependency_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_dependencies.id", ondelete="RESTRICT")
    )
    # Users live in the identity module: referenced by id, resolved via its directory.
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    customer_name: Mapped[str | None] = mapped_column(String(200))
    customer_nit: Mapped[str | None] = mapped_column(String(30))
    supplier_name: Mapped[str | None] = mapped_column(String(200))
    sales_executive_name: Mapped[str | None] = mapped_column(String(200))
    requester_name: Mapped[str | None] = mapped_column(String(200))
    requester_email: Mapped[str | None] = mapped_column(String(320))
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_requests.id", ondelete="SET NULL")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    first_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    legacy: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CaseItemModel(Base):
    """Requested document of a case."""

    __tablename__ = "pqrsf_case_items"
    __table_args__ = (
        Index("ix_pqrsf_case_items_case", "case_id", "sort_order"),
        CheckConstraint("quantity IS NULL OR quantity > 0", name="ck_pqrsf_case_items_quantity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_cases.id", ondelete="CASCADE"), nullable=False
    )
    document_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_document_types.id", ondelete="RESTRICT")
    )
    description: Mapped[str | None] = mapped_column(String(300))
    quantity: Mapped[int | None] = mapped_column(Integer)
    sla_days: Mapped[int | None] = mapped_column(Integer)
    due_on: Mapped[date | None] = mapped_column(Date)
    responded_on: Mapped[date | None] = mapped_column(Date)
    response_days: Mapped[int | None] = mapped_column(Integer)
    delay_cause: Mapped[str | None] = mapped_column(String(300))
    delay_detail: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CaseEventModel(Base):
    """Immutable timeline entry of a case."""

    __tablename__ = "pqrsf_case_events"
    __table_args__ = (Index("ix_pqrsf_case_events_case_created", "case_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_cases.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    from_status_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_case_statuses.id", ondelete="RESTRICT")
    )
    to_status_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_case_statuses.id", ondelete="RESTRICT")
    )
    note: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
