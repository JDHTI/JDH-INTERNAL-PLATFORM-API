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
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class RequestTypeModel(Base):
    """Fixed PQRSF request type catalog."""

    __tablename__ = "pqrsf_request_types"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # One short line shown under the option so the requester knows which type to pick.
    description: Mapped[str | None] = mapped_column(String(300))
    # Whether the form asks the requester for the area the request is about.
    asks_area: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    allows_document_request: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    allows_attachments: Mapped[bool] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    prefix: Mapped[str] = mapped_column(String(5), nullable=False, unique=True)
    requires_follow_up: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    sla_days: Mapped[int | None] = mapped_column(Integer)
    # ``case`` opens a follow-up case; ``acknowledge`` is stored apart and thanked.
    handling: Mapped[str] = mapped_column(String(12), nullable=False, server_default="case")
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
    description: Mapped[str | None] = mapped_column(Text)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_subjects.id", ondelete="SET NULL")
    )
    cause_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_causes.id", ondelete="SET NULL")
    )
    # [{"invoice_number", "sku", "description", "supplier_code", "supplier_name", "lot",
    #   "expiry_date", "affected_quantity"}, ...]; names are copies taken at submission.
    products: Mapped[list | None] = mapped_column(JSONB)
    # ``yes`` | ``no`` | ``unknown``: whether a pickup order was already requested in the ERP.
    pickup_order_requested: Mapped[str | None] = mapped_column(String(8))
    pickup_order_number: Mapped[str | None] = mapped_column(String(40))
    # When the order with the products was delivered; asked only with products.
    delivery_date: Mapped[date | None] = mapped_column(Date)
    # Area the requester picked; decides only when no cause, subject or category does.
    dependency_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_dependencies.id", ondelete="SET NULL")
    )
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


class SubjectModel(Base):
    """What a request is about (the "novedad" of the communication channels table)."""

    __tablename__ = "pqrsf_subjects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300))
    # Codes of the request types that offer it (``queja``, ``reclamo``…).
    request_type_codes: Mapped[list[str]] = mapped_column(
        ARRAY(String(30)), nullable=False, server_default="{}"
    )
    # Who attends it when it has no causes, or the cause names none.
    dependency_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_dependencies.id", ondelete="SET NULL")
    )
    is_other: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CauseModel(Base):
    """The cause of a subject; it decides the dependency and the treatment route."""

    __tablename__ = "pqrsf_causes"
    __table_args__ = (
        Index("ix_pqrsf_causes_subject", "subject_id", "sort_order"),
        CheckConstraint("route IN ('supplier', 'internal', 'none')", name="ck_pqrsf_causes_route"),
        CheckConstraint("NOT requires_product OR allows_product", name="ck_pqrsf_causes_product"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_subjects.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300))
    dependency_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_dependencies.id", ondelete="SET NULL")
    )
    # ``supplier``: may be escalated to the manufacturer; ``internal``: a process failure.
    route: Mapped[str] = mapped_column(String(10), nullable=False, server_default="none")
    requires_product: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    allows_product: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_other: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
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
    allows_direct_upload: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
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
        Index("ix_pqrsf_cases_sales_executive", "sales_executive_user_id"),
        Index("ix_pqrsf_cases_requested_area", "requested_area_id"),
        Index("ix_pqrsf_cases_subject", "subject_id"),
        Index("ix_pqrsf_cases_cause", "cause_id"),
        Index("ix_pqrsf_cases_news_at", "news_at", postgresql_where=text("news_at IS NOT NULL")),
        CheckConstraint(
            "resolution IS NULL OR resolution IN ('procede', 'no_procede')",
            name="ck_pqrsf_cases_resolution",
        ),
        CheckConstraint(
            "route IS NULL OR route IN ('supplier', 'internal', 'none')",
            name="ck_pqrsf_cases_route",
        ),
        CheckConstraint(
            "pickup_order_requested IS NULL OR pickup_order_requested IN ('yes', 'no', 'unknown')",
            name="ck_pqrsf_cases_pickup_order",
        ),
        CheckConstraint(
            "sales_executive_source IS NULL "
            "OR sales_executive_source IN ('invoice', 'internal', 'manual')",
            name="ck_pqrsf_cases_sales_executive_source",
        ),
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
    # The seller of the invoice (from the sales data), the comercial who filed it, or typed.
    sales_executive_code: Mapped[str | None] = mapped_column(String(40))
    sales_executive_email: Mapped[str | None] = mapped_column(String(320))
    sales_executive_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # ``invoice`` | ``internal`` | ``manual``.
    sales_executive_source: Mapped[str | None] = mapped_column(String(10))
    requester_name: Mapped[str | None] = mapped_column(String(200))
    requester_email: Mapped[str | None] = mapped_column(String(320))
    # Classification of the case: the area the requester picked, the subject and cause,
    # and the route of that cause, copied when the case opens.
    requested_area_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_dependencies.id", ondelete="SET NULL")
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_subjects.id", ondelete="SET NULL")
    )
    cause_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_causes.id", ondelete="SET NULL")
    )
    # ``supplier`` | ``internal`` | ``none``.
    route: Mapped[str | None] = mapped_column(String(10))
    # ``yes`` | ``no`` | ``unknown``; the number is known later, once the order is in the ERP.
    pickup_order_requested: Mapped[str | None] = mapped_column(String(8))
    pickup_order_number: Mapped[str | None] = mapped_column(String(40))
    delivery_date: Mapped[date | None] = mapped_column(Date)
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_requests.id", ondelete="SET NULL")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    first_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # An email from the client or a supplier the team has not opened yet; set and cleared
    # on its own (never through the header save), so a concurrent edit cannot undo it.
    news_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # How the case ended (``procede`` | ``no_procede``) and whether a JDH process failed.
    resolution: Mapped[str | None] = mapped_column(String(12))
    internal_failure: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # An improvement opportunity went to the supplier; set on its own, like ``news_at``.
    om_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    legacy: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CaseProductModel(Base):
    """A product line of a case, as the requester sent it (it is not edited)."""

    __tablename__ = "pqrsf_case_products"
    __table_args__ = (
        Index("ix_pqrsf_case_products_case", "case_id", "sort_order"),
        Index("ix_pqrsf_case_products_supplier", "supplier_code"),
        Index("ix_pqrsf_case_products_sku", "sku"),
        CheckConstraint(
            "source IN ('request', 'internal', 'email')", name="ck_pqrsf_case_products_source"
        ),
        CheckConstraint("affected_quantity > 0", name="ck_pqrsf_case_products_affected_quantity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_cases.id", ondelete="CASCADE"), nullable=False
    )
    # ``request`` (public form), ``internal`` (a collaborator) or ``email``.
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    invoice_number: Mapped[str] = mapped_column(String(40), nullable=False)
    sku: Mapped[str] = mapped_column(String(40), nullable=False)
    # Copies of the product master at submission, so they read the same later.
    description: Mapped[str | None] = mapped_column(Text)
    supplier_code: Mapped[str | None] = mapped_column(String(60))
    supplier_name: Mapped[str | None] = mapped_column(String(300))
    lot: Mapped[str] = mapped_column(String(60), nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    affected_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
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
        Index("ix_pqrsf_case_attachments_comment", "comment_event_id"),
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
    comment_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_case_events.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FeedbackModel(Base):
    """A suggestion or compliment, kept apart from the cases."""

    __tablename__ = "pqrsf_feedback"
    __table_args__ = (
        UniqueConstraint("request_id", name="pqrsf_feedback_request_id_key"),
        UniqueConstraint("email_message_id", name="pqrsf_feedback_email_message_id_key"),
        Index("ix_pqrsf_feedback_type_created", "request_type_id", "created_at"),
        Index("ix_pqrsf_feedback_created", "created_at"),
        Index("ix_pqrsf_feedback_email", "email"),
        CheckConstraint("origin IN ('public_form', 'email')", name="ck_pqrsf_feedback_origin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pqrsf_request_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    origin: Mapped[str] = mapped_column(String(12), nullable=False)
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_requests.id", ondelete="SET NULL")
    )
    email_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_email_messages.id", ondelete="SET NULL")
    )
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_sites.id", ondelete="SET NULL")
    )
    # Area the sender picked on the public form.
    dependency_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_dependencies.id", ondelete="SET NULL")
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    business_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    reply_notification_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reply_skipped_reason: Mapped[str | None] = mapped_column(String(40))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FeedbackFileModel(Base):
    """File metadata of an attachment of a suggestion or compliment."""

    __tablename__ = "pqrsf_feedback_files"
    __table_args__ = (Index("ix_pqrsf_feedback_files_feedback", "feedback_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    feedback_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_feedback.id", ondelete="CASCADE"), nullable=False
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


class CertificateModel(Base):
    """A document type of the public form answered automatically with a file of the manager."""

    __tablename__ = "pqrsf_certificates"
    __table_args__ = (
        UniqueConstraint("document_type_id", name="pqrsf_certificates_document_type_id_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    document_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pqrsf_document_types.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DocumentDeliveryModel(Base):
    """The log of a request answered automatically with certificates."""

    __tablename__ = "pqrsf_document_deliveries"
    __table_args__ = (
        UniqueConstraint("request_id", name="pqrsf_document_deliveries_request_id_key"),
        Index("ix_pqrsf_document_deliveries_created", "created_at"),
        Index("ix_pqrsf_document_deliveries_email", "email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_requests.id", ondelete="SET NULL")
    )
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_sites.id", ondelete="SET NULL")
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    business_name: Mapped[str | None] = mapped_column(String(200))
    nit: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    items: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    manual_items: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    case_code: Mapped[str | None] = mapped_column(String(20))
    notification_ids: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ItemDeliveryModel(Base):
    """A requested document of a case answered with a file (linked from the manager or uploaded)."""

    __tablename__ = "pqrsf_item_deliveries"
    __table_args__ = (
        Index("ix_pqrsf_item_deliveries_case", "case_id"),
        Index("ix_pqrsf_item_deliveries_item", "item_id"),
        CheckConstraint("source IN ('vault', 'upload')", name="ck_pqrsf_item_deliveries_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_cases.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_case_items.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL")
    )
    attachment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_case_attachments.id", ondelete="SET NULL")
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    notification_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SupplierContactModel(Base):
    """An address a case wrote to as supplier; suggested again for that supplier."""

    __tablename__ = "pqrsf_supplier_contacts"
    __table_args__ = (
        UniqueConstraint("supplier_code", "email", name="pqrsf_supplier_contacts_code_email_key"),
        Index("ix_pqrsf_supplier_contacts_email", "email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    supplier_code: Mapped[str] = mapped_column(String(40), nullable=False, server_default="")
    supplier_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(200))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
        Index("ix_pqrsf_notifications_case", "case_id", "kind"),
        Index("ix_pqrsf_notifications_recipient", "kind", "recipient_email", "created_at"),
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
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
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


class DocumentTemplateModel(Base):
    """A Word template kept in the bucket (the OM, P.06); one active per kind."""

    __tablename__ = "pqrsf_document_templates"
    __table_args__ = (
        CheckConstraint("kind IN ('om')", name="ck_pqrsf_document_templates_kind"),
        Index(
            "ix_pqrsf_document_templates_active",
            "kind",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    version_label: Mapped[str] = mapped_column(String(60), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OmSequenceModel(Base):
    """The next OM number (one row); Calidad sets where it starts."""

    __tablename__ = "pqrsf_om_sequence"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_pqrsf_om_sequence_single"),
        CheckConstraint("next_number > 0", name="ck_pqrsf_om_sequence_positive"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    next_number: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ImprovementOpportunityModel(Base):
    """An improvement opportunity (OM, P.06) of a case for one supplier."""

    __tablename__ = "pqrsf_improvement_opportunities"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'sent')", name="ck_pqrsf_improvement_opportunities_status"
        ),
        UniqueConstraint("number", name="pqrsf_improvement_opportunities_number_key"),
        Index("ix_pqrsf_improvement_opportunities_case", "case_id", "created_at"),
        Index(
            "ux_pqrsf_improvement_opportunities_supplier",
            "case_id",
            text("coalesce(supplier_code, '')"),
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_cases.id", ondelete="CASCADE"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    supplier_code: Mapped[str | None] = mapped_column(String(40))
    supplier_name: Mapped[str | None] = mapped_column(String(200))
    immediate_actions: Mapped[str | None] = mapped_column(Text)
    request_note: Mapped[str | None] = mapped_column(Text)
    document_number: Mapped[str | None] = mapped_column(String(200))
    document_date: Mapped[date | None] = mapped_column(Date)
    evidence_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    signer_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    signer_name: Mapped[str | None] = mapped_column(String(200))
    signer_position: Mapped[str | None] = mapped_column(String(200))
    signer_email: Mapped[str | None] = mapped_column(String(320))
    signer_phone: Mapped[str | None] = mapped_column(String(60))
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pqrsf_document_templates.id", ondelete="SET NULL")
    )
    docx_key: Mapped[str | None] = mapped_column(String(500))
    pdf_key: Mapped[str | None] = mapped_column(String(500))
    edited: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="draft")
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
