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
    UniqueConstraint,
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
        CheckConstraint(
            "jdh_net_cost IS NULL OR (calc_method = 'costo_neto' AND jdh_net_cost >= 0)",
            name="ck_support_jdh_net_cost",
        ),
        CheckConstraint(
            "supported_margin_pct IS NULL OR "
            "(supported_margin_pct >= 0 AND supported_margin_pct < 1)",
            name="ck_supplier_supports_supported_margin",
        ),
        CheckConstraint(
            "supplier_min_margin_pct IS NULL OR "
            "(supplier_min_margin_pct > -1 AND supplier_min_margin_pct < 1)",
            name="ck_supplier_supports_supplier_min_margin",
        ),
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
    # Covers every customer of ``customer_group`` (a copy of a business group's support).
    group_wide: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
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
    promo_warehouses: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    # Benefit
    calc_method: Mapped[str] = mapped_column(String(30), nullable=False)
    supplier_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    jdh_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    jdh_net_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    value_unit: Mapped[str | None] = mapped_column(String(10))
    target_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    min_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    list_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    # Margin the product may drop to on top of the supplier's support (supported price).
    supported_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    # % Margen Garantizado: the supplier's minimum margin, the one the supported price is set
    # at (2026-11-23); it may be negative (the supplier's exception).
    supplier_min_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
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
    # The bulk load and the action (``carga``, ``manual``, ``aprobacion_jdh``…) that last
    # wrote the support; every version is kept in ``dwh_outbox``.
    last_load_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("supplier_support_loads.id", ondelete="SET NULL")
    )
    last_origin: Mapped[str | None] = mapped_column(String(20))
    # The internal support request it was born from (a bulk load never closes those).
    internal_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("internal_support_requests.id", ondelete="SET NULL")
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


class SupplierSupportLoadModel(Base):
    """One bulk load of supports: the file, who uploaded it and what it did."""

    __tablename__ = "supplier_support_loads"
    __table_args__ = (Index("ix_supplier_support_loads_created", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    supplier_code: Mapped[str | None] = mapped_column(Text)
    sheet: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    rows_read: Mapped[int] = mapped_column(Integer, nullable=False)
    created: Mapped[int] = mapped_column(Integer, nullable=False)
    updated: Mapped[int] = mapped_column(Integer, nullable=False)
    unchanged: Mapped[int] = mapped_column(Integer, nullable=False)
    missing: Mapped[int] = mapped_column(Integer, nullable=False)
    expired: Mapped[int] = mapped_column(Integer, nullable=False)


class SupplierSupportCostModel(Base):
    """The cost of a support's SKU from the DWH, with the quoter's rule (refreshed daily)."""

    __tablename__ = "supplier_support_costs"

    support_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("supplier_supports.id", ondelete="CASCADE"), primary_key=True
    )
    product_sku: Mapped[str] = mapped_column(Text, nullable=False)
    plan_key: Mapped[str] = mapped_column(Text, nullable=False)
    plan_label: Mapped[str] = mapped_column(Text, nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    source: Mapped[str | None] = mapped_column(String(12))
    quote_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    inventory_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    snapshot_date: Mapped[date | None] = mapped_column(Date)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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


class SupportRequestModel(Base):
    """An immutable proposal with a single terminal decision."""

    __tablename__ = "supplier_support_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pendiente','aprobada','rechazada')", name="ck_support_request_status"
        ),
        CheckConstraint(
            "mode IN ('porcentaje','costo_neto','margen_minimo','margen_proveedor')",
            name="ck_support_request_mode",
        ),
        Index("ix_support_requests_owner_created", "requested_by_id", "created_at"),
        Index("ix_support_requests_status_created", "status", "created_at"),
        Index("ix_support_requests_support", "support_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    support_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("supplier_supports.id", ondelete="RESTRICT"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pendiente")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    support_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(320))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)


class SupportJdhEventModel(Base):
    """Append-only audit, including administrative changes and migration baseline."""

    __tablename__ = "supplier_support_jdh_events"
    __table_args__ = (Index("ix_support_jdh_events_support", "support_id", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    support_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("supplier_supports.id", ondelete="RESTRICT"), nullable=False
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("supplier_support_requests.id", ondelete="RESTRICT")
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reason: Mapped[str | None] = mapped_column(Text)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class InternalSupportRequestModel(Base):
    """A supported margin asked from a customer quotation, decided by BI or a reviewer."""

    __tablename__ = "internal_support_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pendiente', 'aprobada', 'rechazada')",
            name="ck_internal_support_requests_status",
        ),
        CheckConstraint(
            "decision IS NULL OR decision IN ('persistente', 'unica_vez')",
            name="ck_internal_support_requests_decision",
        ),
        CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_to >= valid_from",
            name="ck_internal_support_requests_validity",
        ),
        Index("ix_internal_support_requests_status_created", "status", "created_at"),
        Index("ix_internal_support_requests_owner_created", "requested_by_id", "created_at"),
        Index("ix_internal_support_requests_quotation", "quotation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        unique=True,
        server_default=text("nextval('internal_support_request_number_seq')"),
    )
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="pendiente")
    decision: Mapped[str | None] = mapped_column(String(12))
    quotation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quotations.id", ondelete="SET NULL")
    )
    quotation_label: Mapped[str | None] = mapped_column(Text)
    customer_nit: Mapped[str] = mapped_column(Text, nullable=False)
    customer_name: Mapped[str | None] = mapped_column(Text)
    commercial_supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("commercial_suppliers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    supplier_code: Mapped[str] = mapped_column(Text, nullable=False)
    supplier_name: Mapped[str | None] = mapped_column(Text)
    requested_for: Mapped[date] = mapped_column(Date, nullable=False)
    agreed_with: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    decided_by: Mapped[str | None] = mapped_column(String(320))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)


class InternalSupportRequestItemModel(Base):
    """One SKU of an internal support request, with the snapshot of the quotation line."""

    __tablename__ = "internal_support_request_items"
    __table_args__ = (
        UniqueConstraint("request_id", "sku", name="uq_internal_support_request_items_sku"),
        CheckConstraint(
            "requested_margin_pct >= 0 AND requested_margin_pct < 1",
            name="ck_internal_support_request_items_requested",
        ),
        CheckConstraint(
            "approved_margin_pct IS NULL OR (approved_margin_pct >= 0 AND approved_margin_pct < 1)",
            name="ck_internal_support_request_items_approved",
        ),
        Index("ix_internal_support_request_items_request", "request_id", "position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("internal_support_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(Text)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    quoted_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    min_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    requested_margin_pct: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    approved_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    support_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("supplier_supports.id", ondelete="SET NULL")
    )
    # The product's optimal margin on the customer's list when it was asked (0070).
    target_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
