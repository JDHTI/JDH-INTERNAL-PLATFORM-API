"""Tables of the support settlement (migration ``0085_support_settlements``).

- ``support_settlements``: one per month, draft or closed, with its totals.
- ``support_settlement_refs``: every supported reference of the month: all its sales (to
  recompose its margin) and what its supports pay.
- ``support_settlement_sales``: the sale lines a support covered, with the customer's
  regional, business unit, channel, city and salesperson (the «reintegro» by dimension).
- ``support_settlement_items``: each support applied to a line, with the support as it was
  when the month was calculated (id, version, type, value, validity).

A recalculation replaces the lines of a draft month in one transaction; a closed month never
changes until the area leader reopens it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

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
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base

_MONEY = Numeric(20, 4)
_QTY = Numeric(18, 4)


class SupportSettlementModel(Base):
    """The settlement of one month."""

    __tablename__ = "support_settlements"
    __table_args__ = (
        CheckConstraint("status IN ('borrador','cerrado')", name="ck_support_settlements_status"),
        CheckConstraint(
            "period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'", name="ck_support_settlements_period"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    period: Mapped[str] = mapped_column(String(7), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="borrador")
    amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False, server_default="0")
    flagged_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False, server_default="0")
    net: Mapped[Decimal] = mapped_column(_MONEY, nullable=False, server_default="0")
    cost: Mapped[Decimal] = mapped_column(_MONEY, nullable=False, server_default="0")
    sales_read: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    supported_lines: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    suppliers: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    calculated_by: Mapped[str] = mapped_column(String(320), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(320))
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_by: Mapped[str | None] = mapped_column(String(320))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SupportSettlementReferenceModel(Base):
    """A supported reference of the month: all its sales and what its supports pay."""

    __tablename__ = "support_settlement_refs"
    __table_args__ = (
        UniqueConstraint("settlement_id", "sku", name="uq_support_settlement_refs_sku"),
        Index("ix_support_settlement_refs_supplier", "settlement_id", "supplier_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    settlement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("support_settlements.id", ondelete="CASCADE"), nullable=False
    )
    supplier_code: Mapped[str | None] = mapped_column(Text)
    supplier_name: Mapped[str | None] = mapped_column(Text)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(_QTY, nullable=False)
    net: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    cost: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    flagged_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    supported_lines: Mapped[int] = mapped_column(Integer, nullable=False)


class SupportSettlementSaleModel(Base):
    """A sale line a support covered."""

    __tablename__ = "support_settlement_sales"
    __table_args__ = (
        UniqueConstraint("settlement_id", "line_key", name="uq_support_settlement_sales_line"),
        Index("ix_support_settlement_sales_supplier", "settlement_id", "supplier_code", "sku"),
        Index("ix_support_settlement_sales_date", "settlement_id", "sale_date"),
        Index("ix_support_settlement_sales_nit", "settlement_id", "nit"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    settlement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("support_settlements.id", ondelete="CASCADE"), nullable=False
    )
    line_key: Mapped[str] = mapped_column(Text, nullable=False)
    sale_date: Mapped[date] = mapped_column(Date, nullable=False)
    document: Mapped[str | None] = mapped_column(Text)
    co: Mapped[str | None] = mapped_column(Text)
    co_name: Mapped[str | None] = mapped_column(Text)
    regional: Mapped[str | None] = mapped_column(Text)
    business_unit: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    salesperson: Mapped[str | None] = mapped_column(Text)
    nit: Mapped[str | None] = mapped_column(Text)
    branch_id: Mapped[str | None] = mapped_column(Text)
    customer_name: Mapped[str | None] = mapped_column(Text)
    business_group: Mapped[str | None] = mapped_column(Text)
    supplier_code: Mapped[str | None] = mapped_column(Text)
    supplier_name: Mapped[str | None] = mapped_column(Text)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    warehouse: Mapped[str | None] = mapped_column(Text)
    is_return: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(_QTY, nullable=False)
    gross: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    net: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    cost: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    base_unit_cost: Mapped[Decimal | None] = mapped_column(_MONEY)
    cost_source: Mapped[str] = mapped_column(String(12), nullable=False)
    amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    flags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")


class SupportSettlementItemModel(Base):
    """A support applied to a sale line, as the support was when the month was calculated."""

    __tablename__ = "support_settlement_items"
    __table_args__ = (
        Index("ix_support_settlement_items_sale", "sale_id"),
        Index("ix_support_settlement_items_status", "settlement_id", "status"),
        Index("ix_support_settlement_items_support", "settlement_id", "support_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    settlement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("support_settlements.id", ondelete="CASCADE"), nullable=False
    )
    sale_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("support_settlement_sales.id", ondelete="CASCADE"),
        nullable=False,
    )
    # No foreign key: the settlement keeps naming a support deleted later.
    support_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    support_version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    calc_method: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    supplier_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    value_unit: Mapped[str | None] = mapped_column(String(10))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    negotiated_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    settled_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    unit_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


__all__ = [
    "SupportSettlementItemModel",
    "SupportSettlementModel",
    "SupportSettlementReferenceModel",
    "SupportSettlementSaleModel",
]
