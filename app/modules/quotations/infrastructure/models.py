"""SQLAlchemy models of the quoter.

Column names are the field names of :class:`Quotation` and :class:`QuotationLine`, so the
repository maps them generically.
"""

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
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class QuotationModel(Base):
    """A quotation of one customer: the customer as it was and the state of the update."""

    __tablename__ = "quotations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('borrador', 'pendiente_validacion', 'creada', 'actualizada')",
            name="ck_quotations_status",
        ),
        CheckConstraint("window_months IN (6, 12)", name="ck_quotations_window"),
        CheckConstraint(
            "list_action IN ('actualizacion', 'creacion')", name="ck_quotations_list_action"
        ),
        CheckConstraint("regional IN ('nacional', 'costa')", name="ck_quotations_regional"),
        Index("ix_quotations_owner_created", "created_by_id", "created_at"),
        Index("ix_quotations_customer", "customer_nit"),
        Index("ix_quotations_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    customer_nit: Mapped[str] = mapped_column(Text, nullable=False)
    customer_name: Mapped[str | None] = mapped_column(Text)
    channel_code: Mapped[str | None] = mapped_column(String(10))
    channel: Mapped[str | None] = mapped_column(Text)
    price_list_id: Mapped[str] = mapped_column(String(3), nullable=False)
    price_list_name: Mapped[str | None] = mapped_column(Text)
    price_list_customers: Mapped[int | None] = mapped_column(Integer)
    billing_center_code: Mapped[str | None] = mapped_column(String(10))
    salesperson_code: Mapped[str | None] = mapped_column(String(20))
    salesperson_name: Mapped[str | None] = mapped_column(Text)
    window_months: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    activation_date: Mapped[date | None] = mapped_column(Date)
    export_price_list_id: Mapped[str | None] = mapped_column(String(3))
    warnings: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cost_cut_date: Mapped[date | None] = mapped_column(Date)
    max_points_below_target: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    changed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    below_rule_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(320), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    list_action: Mapped[str] = mapped_column(String(20), nullable=False, default="actualizacion")
    new_list_name: Mapped[str | None] = mapped_column(String(40))
    regional: Mapped[str] = mapped_column(String(10), nullable=False, default="nacional")
    price_family: Mapped[str] = mapped_column(String(20), nullable=False, default="generales")
    stock_source: Mapped[str | None] = mapped_column(Text)
    below_target_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[str | None] = mapped_column(String(320))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validated_by: Mapped[str | None] = mapped_column(String(320))
    review_note: Mapped[str | None] = mapped_column(Text)


class QuotationLineModel(Base):
    """One SKU of a quotation: its snapshot and the advisor's price."""

    __tablename__ = "quotation_lines"
    __table_args__ = (
        UniqueConstraint("quotation_id", "sku", name="uq_quotation_lines_sku"),
        Index("ix_quotation_lines_position", "quotation_id", "position"),
        CheckConstraint("origin IN ('historico', 'agregado')", name="ck_quotation_lines_origin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    quotation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quotations.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(10))
    pack_factor: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    supplier_code: Mapped[str | None] = mapped_column(Text)
    supplier_name: Mapped[str | None] = mapped_column(Text)
    last_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    last_gross_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    last_discount_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    last_sale_date: Mapped[date | None] = mapped_column(Date)
    last_document: Mapped[str | None] = mapped_column(Text)
    last_sale_unit: Mapped[str | None] = mapped_column(String(10))
    last_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    sales_in_window: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    list_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    vat_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    value_tax_key: Mapped[str | None] = mapped_column(String(10))
    value_tax_name: Mapped[str | None] = mapped_column(Text)
    value_tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    quote_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    discarded_quote_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    inventory_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    cost_source: Mapped[str | None] = mapped_column(String(20))
    optimal_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    below_target: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    target_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    margin_basis: Mapped[str | None] = mapped_column(String(20))
    min_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    current_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    current_margin_with_support_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    min_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    support: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    support_candidates: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    new_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    new_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    new_margin_with_support_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    variation_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    compliance: Mapped[str | None] = mapped_column(String(20))
    problems: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    origin: Mapped[str] = mapped_column(String(20), nullable=False, default="historico")
