"""Tables of Gestión comercial (migration ``0093_commercial_budget``).

- ``sales_month_customers``: a month of sales by customer branch, copied from
  ``consumo.ventas`` (net sale, cost and lines); ``sales_month_loads`` says which months were
  copied and when.
- ``sales_reps``: the rep master (``consumo.vendedores``), copied daily.
- ``sales_budget_periods``: a month of the budget, open or closed.
- ``sales_budgets``: a rep's budget in a month, every field optional; ``sales_budget_changes``
  is its history.
- ``sales_budget_snapshot_reps`` and ``sales_budget_snapshot_customers``: the frozen picture
  of a closed month (every row, and who had which customer).
- ``vendor_master_uploads``: every upload of the rep master to the warehouse (never the file:
  it carries personal data).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base

_MONEY = Numeric(20, 4)
_RATIO = Numeric(12, 6)
_COUNT = Numeric(14, 4)
_PERIOD = "period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'"


class SalesMonthCustomerModel(Base):
    """A customer branch's sales of one month."""

    __tablename__ = "sales_month_customers"

    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    customer_key: Mapped[str] = mapped_column(Text, primary_key=True)
    net: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    cost: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    lines: Mapped[int] = mapped_column(Integer, nullable=False)


class SalesMonthLoadModel(Base):
    """A month copied from the warehouse."""

    __tablename__ = "sales_month_loads"

    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    customers: Mapped[int] = mapped_column(Integer, nullable=False)
    lines: Mapped[int] = mapped_column(Integer, nullable=False)
    net: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)


class SalesRepModel(Base):
    """A rep of the master (``consumo.vendedores``)."""

    __tablename__ = "sales_reps"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    team: Mapped[str | None] = mapped_column(Text)
    team_class: Mapped[str | None] = mapped_column(Text)
    regional: Mapped[str | None] = mapped_column(Text)
    business_unit: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SalesBudgetPeriodModel(Base):
    """A month of the budget. ``version`` grows with every budget change, so a close
    computed before a change is refused."""

    __tablename__ = "sales_budget_periods"
    __table_args__ = (
        CheckConstraint("status IN ('abierto','cerrado')", name="ck_sales_budget_periods_status"),
        CheckConstraint(_PERIOD, name="ck_sales_budget_periods_period"),
    )

    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="abierto")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(320))
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_by: Mapped[str | None] = mapped_column(String(320))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class SalesBudgetModel(Base):
    """A rep's budget in a month."""

    __tablename__ = "sales_budgets"
    __table_args__ = (
        UniqueConstraint("period", "salesperson_code", name="uq_sales_budgets_period_rep"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    salesperson_code: Mapped[str] = mapped_column(Text, nullable=False)
    budget_sales: Mapped[Decimal | None] = mapped_column(_MONEY)
    budget_margin: Mapped[Decimal | None] = mapped_column(_RATIO)
    budget_customers: Mapped[int | None] = mapped_column(Integer)
    budget_collection: Mapped[Decimal | None] = mapped_column(_MONEY)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(320), nullable=False)


class SalesBudgetChangeModel(Base):
    """One change of a rep's budget: the fields before and after."""

    __tablename__ = "sales_budget_changes"
    __table_args__ = (
        Index("ix_sales_budget_changes_period", "period", "at"),
        Index("ix_sales_budget_changes_rep", "period", "salesperson_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    salesperson_code: Mapped[str] = mapped_column(Text, nullable=False)
    before: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    after: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(12), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(320), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SalesBudgetSnapshotRepModel(Base):
    """A rep's row of a closed month, as it was when closed."""

    __tablename__ = "sales_budget_snapshot_reps"

    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    regional: Mapped[str | None] = mapped_column(Text)
    business_unit: Mapped[str | None] = mapped_column(Text)
    team: Mapped[str | None] = mapped_column(Text)
    team_class: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    current_net: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    current_margin: Mapped[Decimal | None] = mapped_column(_RATIO)
    current_customers: Mapped[Decimal] = mapped_column(_COUNT, nullable=False)
    average_net: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    average_margin: Mapped[Decimal | None] = mapped_column(_RATIO)
    average_customers: Mapped[Decimal] = mapped_column(_COUNT, nullable=False)
    average_months: Mapped[int | None] = mapped_column(Integer)
    last_year_net: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    last_year_margin: Mapped[Decimal | None] = mapped_column(_RATIO)
    last_year_customers: Mapped[Decimal] = mapped_column(_COUNT, nullable=False)
    budget_sales: Mapped[Decimal | None] = mapped_column(_MONEY)
    budget_margin: Mapped[Decimal | None] = mapped_column(_RATIO)
    budget_customers: Mapped[int | None] = mapped_column(Integer)
    budget_collection: Mapped[Decimal | None] = mapped_column(_MONEY)


class SalesBudgetSnapshotCustomerModel(Base):
    """Who had a customer branch in a closed month, with its sales that month."""

    __tablename__ = "sales_budget_snapshot_customers"
    __table_args__ = (
        Index("ix_sales_budget_snapshot_customers_rep", "period", "salesperson_code"),
    )

    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    customer_key: Mapped[str] = mapped_column(Text, primary_key=True)
    salesperson_code: Mapped[str | None] = mapped_column(Text)
    salesperson_name: Mapped[str | None] = mapped_column(Text)
    net: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    cost: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    lines: Mapped[int] = mapped_column(Integer, nullable=False)


class VendorMasterUploadModel(Base):
    """An upload of the rep master to dwh-platform and what it answered."""

    __tablename__ = "vendor_master_uploads"
    __table_args__ = (Index("ix_vendor_master_uploads_at", "at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    # ``cargado`` (dwh-platform took it), ``rechazado`` (it answered 4xx) or ``error``.
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    rejected_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    warnings: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # dwh-platform's counts, or its error message.
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    actor: Mapped[str] = mapped_column(String(320), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VendorMasterTeamModel(Base):
    """A team of the rep master kept by the platform (migration ``0096``, 2026-11-27): a new
    one not loaded yet, or a warehouse team's class or visibility in the template."""

    __tablename__ = "vendor_master_teams"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    team_class: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(320), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "VendorMasterTeamModel",
    "SalesBudgetChangeModel",
    "SalesBudgetModel",
    "SalesBudgetPeriodModel",
    "SalesBudgetSnapshotCustomerModel",
    "SalesBudgetSnapshotRepModel",
    "SalesMonthCustomerModel",
    "SalesMonthLoadModel",
    "SalesRepModel",
    "VendorMasterUploadModel",
]
