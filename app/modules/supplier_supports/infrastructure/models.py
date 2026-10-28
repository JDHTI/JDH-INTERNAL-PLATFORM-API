"""SQLAlchemy models for the supplier supports master."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base
from app.modules.supplier_supports.domain.vocabulary import (
    CALC_METHODS,
    FUNDINGS,
    KINDS,
    SETTLEMENTS,
    SPECIAL_CASE_PRIORITIES,
    SPECIAL_CASE_STATUSES,
    STATUSES,
    THRESHOLD_TYPES,
    UNIT_BASIS_SOURCES,
    VALUE_UNITS,
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class SupplierSupportModel(Base):
    """One support rule: scope + benefit + validity."""

    __tablename__ = "supplier_supports"
    __table_args__ = (
        CheckConstraint(_in("kind", KINDS), name="ck_supplier_supports_kind"),
        CheckConstraint(_in("funding", FUNDINGS), name="ck_supplier_supports_funding"),
        CheckConstraint(_in("calc_method", CALC_METHODS), name="ck_supplier_supports_calc_method"),
        CheckConstraint(_in("status", STATUSES), name="ck_supplier_supports_status"),
        CheckConstraint(
            f"settlement_method IS NULL OR {_in('settlement_method', SETTLEMENTS)}",
            name="ck_supplier_supports_settlement",
        ),
        CheckConstraint(
            f"value_unit IS NULL OR {_in('value_unit', VALUE_UNITS)}",
            name="ck_supplier_supports_value_unit",
        ),
        CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_to >= valid_from",
            name="ck_supplier_supports_valid_range",
        ),
        CheckConstraint(
            _in("unit_basis_source", UNIT_BASIS_SOURCES),
            name="ck_supplier_supports_unit_basis_source",
        ),
        CheckConstraint(
            "kind = 'excepcion_margen' OR commercial_supplier_id IS NOT NULL",
            name="ck_supplier_supports_supplier_required",
        ),
        CheckConstraint(
            "(status = 'anulado') = (voided_at IS NOT NULL)",
            name="ck_supplier_supports_void_consistent",
        ),
        Index("ix_supplier_supports_supplier", "commercial_supplier_id"),
        Index("ix_supplier_supports_customer_nit", "customer_nit"),
        Index("ix_supplier_supports_product_id", "product_id"),
        Index("ix_supplier_supports_product_sku", "product_sku"),
        Index("ix_supplier_supports_validity", "valid_from", "valid_to"),
        Index("ix_supplier_supports_status", "status"),
        Index("ix_supplier_supports_kind", "kind"),
        Index("ix_supplier_supports_regionals", "regionals", postgresql_using="gin"),
        Index("ix_supplier_supports_business_units", "business_units", postgresql_using="gin"),
        Index("ix_supplier_supports_channels", "channels", postgresql_using="gin"),
        Index("ix_supplier_supports_issues", "issues", postgresql_using="gin"),
        Index(
            "uq_supplier_supports_source_key",
            "source_key",
            unique=True,
            postgresql_where=text("source_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)

    # Who and what it is
    commercial_supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commercial_suppliers.id", ondelete="RESTRICT")
    )
    external_ref: Mapped[str | None] = mapped_column(Text)
    program: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    funding: Mapped[str] = mapped_column(String(10), nullable=False, server_default="proveedor")
    settlement_method: Mapped[str | None] = mapped_column(String(30))
    settlement_frequency: Mapped[str | None] = mapped_column(Text)

    # Scope (NULL or empty array = all)
    customer_nit: Mapped[str | None] = mapped_column(Text)
    customer_branch_id: Mapped[str | None] = mapped_column(Text)
    customer_name: Mapped[str | None] = mapped_column(Text)
    customer_group: Mapped[str | None] = mapped_column(Text)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT")
    )
    product_sku: Mapped[str | None] = mapped_column(Text)
    supplier_sku: Mapped[str | None] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(Text)
    regionals: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    business_units: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    channels: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    warehouse: Mapped[str | None] = mapped_column(Text)

    # Benefit
    calc_method: Mapped[str] = mapped_column(String(30), nullable=False)
    supplier_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    jdh_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    value_unit: Mapped[str | None] = mapped_column(String(10))
    target_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    min_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    list_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    pack_factor: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    reference_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    reference_list_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    reference_date: Mapped[date | None] = mapped_column(Date)

    # Conditions
    min_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    committed_volume: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    budget_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    budget_units: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    units_used: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    stock_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    conditions: Mapped[str | None] = mapped_column(Text)

    # Validity and state
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    end_condition: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="activo")
    # Reversible switch: a disabled support stays visible (greyed out) but is out of force.
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    unit_basis_source: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="desconocido"
    )
    special_case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("supplier_special_cases.id", ondelete="SET NULL")
    )
    void_reason: Mapped[str | None] = mapped_column(Text)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Traceability
    authorized_by: Mapped[str | None] = mapped_column(Text)
    supplier_contact: Mapped[str | None] = mapped_column(Text)
    sales_rep: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    source_key: Mapped[str | None] = mapped_column(Text)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(320), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SupplierSpecialCaseModel(Base):
    """A particular client case that needs manual configuration and, later, code.

    It works as a request: it tracks what is being done for that client and what business
    logic still has to be programmed.
    """

    __tablename__ = "supplier_special_cases"
    __table_args__ = (
        CheckConstraint(
            _in("status", SPECIAL_CASE_STATUSES), name="ck_supplier_special_cases_status"
        ),
        CheckConstraint(
            _in("priority", SPECIAL_CASE_PRIORITIES), name="ck_supplier_special_cases_priority"
        ),
        Index("ix_supplier_special_cases_status", "status"),
        Index("ix_supplier_special_cases_customer_nit", "customer_nit"),
        Index(
            "uq_supplier_special_cases_source_key",
            "source_key",
            unique=True,
            postgresql_where=text("source_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    commercial_supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commercial_suppliers.id", ondelete="SET NULL")
    )
    customer_nit: Mapped[str | None] = mapped_column(Text)
    customer_name: Mapped[str | None] = mapped_column(Text)
    request: Mapped[str] = mapped_column(Text, nullable=False)
    rule_to_program: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pendiente")
    priority: Mapped[str] = mapped_column(String(10), nullable=False, server_default="media")
    assignee: Mapped[str | None] = mapped_column(String(320))
    resolution: Mapped[str | None] = mapped_column(Text)
    source_key: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(320), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SupplierSupportProfileModel(Base):
    """How one commercial supplier's supports are set up: defaults and file column names.

    ``defaults`` pre-fill the creation form and the bulk load; ``column_map`` says which header
    of the supplier's own file feeds each field, so the file is uploaded as it arrives.
    """

    __tablename__ = "supplier_support_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    commercial_supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("commercial_suppliers.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    defaults: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    column_map: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    sheet: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(320), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SupplierSupportAttachmentModel(Base):
    """A document of the document manager that backs a support (a screenshot of an e-mail...).

    Many to many: one document can back every support of a negotiation, and a support can
    have several documents. The bytes and the folder live in the documents module.
    """

    __tablename__ = "supplier_support_documents"
    __table_args__ = (Index("ix_supplier_support_documents_document", "document_id"),)

    support_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("supplier_supports.id", ondelete="CASCADE"), primary_key=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    linked_by: Mapped[str] = mapped_column(String(320), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SupplierSupportTierModel(Base):
    """A volume or sales threshold of a support and the value it unlocks."""

    __tablename__ = "supplier_support_tiers"
    __table_args__ = (
        CheckConstraint(
            _in("threshold_type", THRESHOLD_TYPES), name="ck_supplier_support_tiers_type"
        ),
        CheckConstraint(
            "to_value IS NULL OR to_value >= from_value", name="ck_supplier_support_tiers_range"
        ),
        Index("ix_supplier_support_tiers_support", "support_id", "position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    support_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("supplier_supports.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    threshold_type: Mapped[str] = mapped_column(String(20), nullable=False)
    from_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    to_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    value: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
