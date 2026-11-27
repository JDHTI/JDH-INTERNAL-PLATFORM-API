"""SQLAlchemy models for the retained orders validator."""

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
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base
from app.modules.order_validation.domain.vocabulary import REASONS, STATUSES


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class OrderValidationBatchModel(Base):
    """An uploaded retained orders file and the counts of its latest validation."""

    __tablename__ = "order_validation_batches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(320), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    validated_by: Mapped[str] = mapped_column(String(320), nullable=False)
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    order_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    ready_line_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    ready_order_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Orders a person marked as released; not part of ready_order_count.
    released_order_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Margin a line may fall short by and still be released, as a fraction (0.005 = 0,5 pts).
    margin_threshold: Mapped[Decimal] = mapped_column(
        Numeric(8, 6), nullable=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OrderValidationApprovalModel(Base):
    """A pending order a person released by hand; it survives re-validations."""

    __tablename__ = "order_validation_approvals"
    __table_args__ = (
        UniqueConstraint("batch_id", "document", name="uq_order_validation_approvals_document"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("order_validation_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    document: Mapped[str] = mapped_column(Text, nullable=False)
    approved_by: Mapped[str] = mapped_column(String(320), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))


class OrderValidationLineModel(Base):
    """One line of an uploaded file and its latest result."""

    __tablename__ = "order_validation_lines"
    __table_args__ = (
        CheckConstraint(_in("status", STATUSES), name="ck_order_validation_lines_status"),
        CheckConstraint(_in("reason", REASONS), name="ck_order_validation_lines_reason"),
        Index("ix_order_validation_lines_batch", "batch_id", "row_number"),
        Index("ix_order_validation_lines_document", "batch_id", "document"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("order_validation_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[str] = mapped_column(Text, nullable=False)
    order_date: Mapped[date | None] = mapped_column(Date)
    operation_center: Mapped[str | None] = mapped_column(Text)
    status_text: Mapped[str | None] = mapped_column(Text)
    customer_nit: Mapped[str | None] = mapped_column(Text)
    customer_name: Mapped[str | None] = mapped_column(Text)
    salesperson: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(Text)
    warehouse: Mapped[str | None] = mapped_column(Text)
    sku: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    supplier_code: Mapped[str | None] = mapped_column(Text)
    supplier_name: Mapped[str | None] = mapped_column(Text)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    price_list: Mapped[str | None] = mapped_column(Text)
    retained: Mapped[str | None] = mapped_column(Text)
    liquor_tax_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    # The average discount at the foot of the invoice, as a fraction (migration 0097).
    discount_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    problems: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str] = mapped_column(String(30), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    # The support that decided the line; a voided or edited support keeps the link.
    support_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("supplier_supports.id", ondelete="SET NULL")
    )
    effective_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    required_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    margin_basis: Mapped[str | None] = mapped_column(String(20))
    liquor_tax: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    liquor_tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    # The product in the line's warehouse when it was validated (migration 0097).
    inventory_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    inventory_stock: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    inventory_date: Mapped[date | None] = mapped_column(Date)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default="[]")
