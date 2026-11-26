"""Tables of Nivel de servicio: reasons, the copied order lines, their reasons and the classes."""

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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class ServiceLevelReasonModel(Base):
    """Why a line was not served («AGOTADO PROVEEDOR») and who answers for it («PROVEEDOR»)."""

    __tablename__ = "service_level_reasons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    responsible: Mapped[str] = mapped_column(String(60), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ServiceLevelLineModel(Base):
    """A line of a fulfilled sales order, copied from the warehouse by the daily job.

    ``line_key`` is the warehouse's key of the SIESA line. A line the warehouse stops giving goes
    away, unless it has a reason: then it stays ``voided`` and out of every figure.
    """

    __tablename__ = "service_level_lines"
    __table_args__ = (
        Index("ix_service_level_lines_order_date_co", "order_date", "co"),
        Index(
            "ix_service_level_lines_pending",
            "order_date",
            postgresql_where=text("qty_pending > 0"),
        ),
        Index("ix_service_level_lines_order_key", "order_key"),
        Index("ix_service_level_lines_sku", "sku"),
        Index("ix_service_level_lines_customer_nit", "customer_nit"),
        Index("ix_service_level_lines_supplier_code", "supplier_code"),
        Index("ix_service_level_lines_seller_key", "seller_key"),
    )

    line_key: Mapped[str] = mapped_column(Text, primary_key=True)
    order_key: Mapped[str] = mapped_column(Text, nullable=False)
    document: Mapped[str | None] = mapped_column(Text)
    co: Mapped[str | None] = mapped_column(Text)
    co_name: Mapped[str | None] = mapped_column(Text)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    erp_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    warehouse_code: Mapped[str | None] = mapped_column(Text)
    warehouse_name: Mapped[str | None] = mapped_column(Text)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    supplier_code: Mapped[str | None] = mapped_column(Text)
    supplier_name: Mapped[str | None] = mapped_column(Text)
    customer_key: Mapped[str | None] = mapped_column(Text)
    customer_nit: Mapped[str | None] = mapped_column(Text)
    customer_name: Mapped[str | None] = mapped_column(Text)
    customer_branch: Mapped[str | None] = mapped_column(Text)
    seller_key: Mapped[str | None] = mapped_column(Text)
    seller_name: Mapped[str | None] = mapped_column(Text)
    qty_ordered: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    qty_dispatched: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    qty_pending: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    value_ordered: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    value_pending: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    voided: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ServiceLevelLineReasonModel(Base):
    """The one reason of a pending line, apart from the copy so the job never overwrites it."""

    __tablename__ = "service_level_line_reasons"
    __table_args__ = (Index("ix_service_level_line_reasons_reason_id", "reason_id"),)

    line_key: Mapped[str] = mapped_column(
        Text,
        ForeignKey("service_level_lines.line_key", ondelete="CASCADE"),
        primary_key=True,
    )
    reason_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("service_level_reasons.id", ondelete="RESTRICT"),
        nullable=False,
    )
    note: Mapped[str | None] = mapped_column(Text)
    assigned_by: Mapped[str] = mapped_column(String(320), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ServiceLevelClassModel(Base):
    """The ABCD class of a product in a C.O. or of a customer's NIT (``co`` empty).

    Only the result is kept; every calculation replaces the whole table.
    """

    __tablename__ = "service_level_classes"
    __table_args__ = (
        CheckConstraint("kind IN ('producto','cliente')", name="ck_service_level_classes_kind"),
        CheckConstraint("abcd IN ('A','B','C','D')", name="ck_service_level_classes_abcd"),
        Index("ix_service_level_classes_kind_key", "kind", "key"),
    )

    kind: Mapped[str] = mapped_column(String(10), primary_key=True)
    co: Mapped[str] = mapped_column(String(20), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    share: Mapped[Decimal] = mapped_column(Numeric(14, 12), nullable=False)
    cumulative: Mapped[Decimal] = mapped_column(Numeric(14, 12), nullable=False)
    abcd: Mapped[str] = mapped_column(String(1), nullable=False)


class ServiceLevelClassRunModel(Base):
    """One calculation of the classes: the closed months it read and the count of each class."""

    __tablename__ = "service_level_class_runs"
    __table_args__ = (Index("ix_service_level_class_runs_computed_at", "computed_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    period_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_to: Mapped[date] = mapped_column(Date, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # ``None``: the daily job.
    computed_by: Mapped[str | None] = mapped_column(String(320))
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


__all__ = [
    "ServiceLevelClassModel",
    "ServiceLevelClassRunModel",
    "ServiceLevelLineModel",
    "ServiceLevelLineReasonModel",
    "ServiceLevelReasonModel",
]
