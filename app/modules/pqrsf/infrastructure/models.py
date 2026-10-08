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
    allows_document_request: Mapped[bool] = mapped_column(nullable=False, server_default="false")
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
    subject: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    document_request: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    document_category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_document_categories.id", ondelete="SET NULL")
    )
    # [{"document_type_id": "<uuid>" | null, "description": "<text>" | null}, ...]
    document_items: Mapped[list | None] = mapped_column(JSONB)
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


class DocumentCategoryModel(Base):
    """Group of document types offered together on the public form.

    ``dependency_id`` is the scope a case opens with when a customer asks for
    documents of this group.
    """

    __tablename__ = "pqrsf_document_categories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    dependency_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_dependencies.id", ondelete="SET NULL")
    )
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
        Index("ix_pqrsf_document_types_category", "category_id"),
        CheckConstraint("sla_days >= 0", name="ck_pqrsf_document_types_sla_days"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sla_days: Mapped[int] = mapped_column(Integer, nullable=False)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_document_categories.id", ondelete="SET NULL")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CaseSequenceModel(Base):
    """Last consecutive used per request type, year and site series."""

    __tablename__ = "pqrsf_case_sequences"
    __table_args__ = (
        PrimaryKeyConstraint("request_type_id", "year", "series", name="pk_pqrsf_case_sequences"),
    )

    request_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pqrsf_request_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    series: Mapped[str] = mapped_column(String(4), nullable=False)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False)


class CaseModel(Base):
    """Follow-up of one PQRSF."""

    __tablename__ = "pqrsf_cases"
    __table_args__ = (
        UniqueConstraint("code", name="pqrsf_cases_code_key"),
        UniqueConstraint("request_id", name="pqrsf_cases_request_id_key"),
        UniqueConstraint(
            "request_type_id",
            "year",
            "series",
            "number",
            name="pqrsf_cases_type_year_series_number_key",
        ),
        CheckConstraint(
            "origin IN ('public_form', 'internal', 'import', 'email')", name="ck_pqrsf_cases_origin"
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
    series: Mapped[str] = mapped_column(String(4), nullable=False)
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
    """Immutable entry of the PQRSF history; it outlives the case it tells about."""

    __tablename__ = "pqrsf_case_events"
    __table_args__ = (
        Index("ix_pqrsf_case_events_case_created", "case_id", "created_at"),
        Index("ix_pqrsf_case_events_created", "created_at"),
        Index(
            "ix_pqrsf_case_events_type_actor_created", "event_type", "actor_user_id", "created_at"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_cases.id", ondelete="SET NULL")
    )
    case_code: Mapped[str | None] = mapped_column(String(30))
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


class EmailMessageModel(Base):
    """An email received at the PQRSF mailbox; its Internet message id deduplicates it."""

    __tablename__ = "pqrsf_email_messages"
    __table_args__ = (
        UniqueConstraint("internet_message_id", name="pqrsf_email_messages_internet_id_key"),
        Index("ix_pqrsf_email_messages_conversation", "conversation_id"),
        Index("ix_pqrsf_email_messages_case", "case_id", "received_at"),
        Index("ix_pqrsf_email_messages_status", "status"),
        CheckConstraint(
            "status IN ('received', 'created', 'threaded', 'ignored', 'failed')",
            name="ck_pqrsf_email_messages_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    internet_message_id: Mapped[str] = mapped_column(String(998), nullable=False)
    graph_id: Mapped[str] = mapped_column(String(512), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(512))
    mailbox: Mapped[str] = mapped_column(String(320), nullable=False)
    sender_address: Mapped[str] = mapped_column(String(320), nullable=False)
    sender_name: Mapped[str | None] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    body_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_cases.id", ondelete="SET NULL")
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CaseAttachmentModel(Base):
    """File metadata of an attachment stored in the bucket under its case."""

    __tablename__ = "pqrsf_case_attachments"
    __table_args__ = (
        UniqueConstraint("storage_key", name="pqrsf_case_attachments_storage_key_key"),
        Index("ix_pqrsf_case_attachments_case", "case_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_cases.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_email_messages.id", ondelete="SET NULL")
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    extension: Mapped[str] = mapped_column(String(10), nullable=False)
    content_type: Mapped[str] = mapped_column(String(127), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MailSyncStateModel(Base):
    """Where the next delta sync of a mailbox starts."""

    __tablename__ = "pqrsf_mail_sync_state"

    mailbox: Mapped[str] = mapped_column(String(320), primary_key=True)
    delta_link: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NotificationModel(Base):
    """An email queued from the PQRSF mailbox; a retry never creates a second row."""

    __tablename__ = "pqrsf_notifications"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="pqrsf_notifications_dedupe_key_key"),
        Index("ix_pqrsf_notifications_due", "status", "next_attempt_at"),
        CheckConstraint(
            "status IN ('pending', 'sent', 'failed')", name="ck_pqrsf_notifications_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False)
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_cases.id", ondelete="SET NULL")
    )
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SettingModel(Base):
    """A module-wide setting kept as JSON (the notification timers)."""

    __tablename__ = "pqrsf_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class JobRunModel(Base):
    """One row per scheduled job and day: the key that stops it from running twice."""

    __tablename__ = "pqrsf_job_runs"

    job: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_date: Mapped[date] = mapped_column(Date, primary_key=True)
    ran_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
