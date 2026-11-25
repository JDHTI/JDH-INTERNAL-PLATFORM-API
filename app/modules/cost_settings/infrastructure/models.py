"""Tables of the cost settings (migration ``0092_cost_settings``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class CostParameterModel(Base):
    """A parameter BI set; a parameter without a row takes its default."""

    __tablename__ = "cost_parameters"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(320), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SupplierBranchRuleModel(Base):
    """Which branch of a supplier quotes for each operation center; one per supplier."""

    __tablename__ = "supplier_branch_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    supplier_nit: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    supplier_name: Mapped[str | None] = mapped_column(String(200))
    general_branch: Mapped[str] = mapped_column(String(10), nullable=False)
    center_branches: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(320), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CostSettingsChangeModel(Base):
    """Every change of a parameter or a rule: before, after, who and when."""

    __tablename__ = "cost_settings_changes"
    __table_args__ = (Index("ix_cost_settings_changes_at", "at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    entity: Mapped[str] = mapped_column(String(30), nullable=False)
    ref: Mapped[str] = mapped_column(String(60), nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(320), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
