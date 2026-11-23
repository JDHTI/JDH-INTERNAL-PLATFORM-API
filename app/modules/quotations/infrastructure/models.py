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
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class PriceListLoadModel(Base):
    """A load of a list's staging in SIESA: what went, when it activates and who loaded it.

    ``rows`` keeps the prices and taxes as they were written (``ExportRow``/``ExportTaxRow``
    as JSON), so the load can be read even after its items change.
    """

    __tablename__ = "price_list_loads"
    __table_args__ = (
        CheckConstraint("kind IN ('lista', 'principal')", name="ck_price_list_loads_kind"),
        Index("ix_price_list_loads_kind_created", "kind", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    price_list_id: Mapped[str] = mapped_column(String(3), nullable=False)
    price_list_name: Mapped[str | None] = mapped_column(Text)
    activation_date: Mapped[date] = mapped_column(Date, nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LiquorTaxUpdateModel(Base):
    """A bulk change of the liquor tax: new Nacional and Costa values per SKU, written to
    every active list where the SKU has a price (``rows`` per SKU, ``export_rows`` per list
    and SKU, fixed when it is validated)."""

    __tablename__ = "liquor_tax_updates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pendiente_validacion', 'pendiente_carga', 'cargada', 'devuelta')",
            name="ck_liquor_tax_updates_status",
        ),
        Index("ix_liquor_tax_updates_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    export_rows: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    note: Mapped[str | None] = mapped_column(Text)
    review_note: Mapped[str | None] = mapped_column(Text)
    activation_date: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    validated_by: Mapped[str | None] = mapped_column(String(320))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uploaded_by: Mapped[str | None] = mapped_column(String(320))
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class QuotationModel(Base):
    """A quotation of one customer: the customer as it was and the state of the update."""

    __tablename__ = "quotations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('borrador', 'pendiente_validacion', 'pendiente_carga', 'cargada')",
            name="ck_quotations_status",
        ),
        CheckConstraint(
            "billing_center_source IN ('auto', 'manual')",
            name="ck_quotations_billing_center_source",
        ),
        CheckConstraint("window_months IN (6, 12)", name="ck_quotations_window"),
        CheckConstraint(
            "list_action IN ('actualizacion', 'creacion')", name="ck_quotations_list_action"
        ),
        CheckConstraint("regional IN ('nacional', 'costa')", name="ck_quotations_regional"),
        CheckConstraint("validity_days BETWEEN 1 AND 365", name="ck_quotations_validity_days"),
        CheckConstraint("offer_scope IN ('changed', 'all')", name="ck_quotations_offer_scope"),
        CheckConstraint("kind IN ('cotizacion', 'lista', 'principal')", name="ck_quotations_kind"),
        CheckConstraint(
            "source IS NULL OR source IN ('cliente', 'lista')", name="ck_quotations_source"
        ),
        Index("ix_quotations_owner_created", "created_by_id", "created_at"),
        Index("ix_quotations_kind_created", "kind", "created_at"),
        Index("ix_quotations_customer", "customer_nit"),
        Index("ix_quotations_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    customer_nit: Mapped[str] = mapped_column(Text, nullable=False)
    customer_name: Mapped[str | None] = mapped_column(Text)
    channel_code: Mapped[str | None] = mapped_column(String(10))
    channel: Mapped[str | None] = mapped_column(Text)
    price_list_id: Mapped[str | None] = mapped_column(String(3))
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
    validity_days: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=30)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    conditions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    include_seller: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    quote_quantities: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    offer_scope: Mapped[str] = mapped_column(String(10), nullable=False, default="changed")
    seller_contact: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cost_centers: Mapped[list[str]] = mapped_column(ARRAY(String(10)), nullable=False, default=list)
    cost_warehouses: Mapped[dict[str, list[str]]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    promo_warehouses: Mapped[dict[str, list[str]]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    billing_center_source: Mapped[str] = mapped_column(String(10), nullable=False, default="auto")
    billing_center_name: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uploaded_by: Mapped[str | None] = mapped_column(String(320))
    temporary_customer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    include_variation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    with_history: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="cotizacion")
    source: Mapped[str | None] = mapped_column(String(20))
    # Quoted to a business group (0074): its name and how many NITs it had.
    business_group: Mapped[str | None] = mapped_column(Text)
    group_customers: Mapped[int | None] = mapped_column(Integer)


class RequestedProductModel(Base):
    """Table of a registry of local products that was dropped before going live.

    Nothing reads or writes it. It stays mapped only so autogenerate and ``alembic check``
    match the schema ``0051`` created instead of proposing to drop it.
    """

    __tablename__ = "quotation_requested_products"
    __table_args__ = (
        CheckConstraint(
            "status IN ('solicitado', 'creado', 'descartado')",
            name="ck_quotation_requested_products_status",
        ),
        Index("ix_quotation_requested_products_status", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    supplier_name: Mapped[str | None] = mapped_column(Text)
    estimated_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    vat_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="solicitado")
    siesa_sku: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(320), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QuotationLineModel(Base):
    """One SKU of a quotation: its snapshot and the advisor's price."""

    __tablename__ = "quotation_lines"
    __table_args__ = (
        UniqueConstraint("quotation_id", "sku", name="uq_quotation_lines_sku"),
        Index("ix_quotation_lines_position", "quotation_id", "position"),
        CheckConstraint(
            "origin IN ('historico', 'agregado', 'local', 'lista')",
            name="ck_quotation_lines_origin",
        ),
        CheckConstraint("quantity IS NULL OR quantity > 0", name="ck_quotation_lines_quantity"),
        CheckConstraint(
            "review_status IN ('sin_marcar', 'por_validar', 'validada')",
            name="ck_quotation_lines_review_status",
        ),
        Index("ix_quotation_lines_requested_product", "requested_product_id"),
        # The staging: validated items not loaded yet.
        Index(
            "ix_quotation_lines_staged",
            "quotation_id",
            postgresql_where=text("review_status = 'validada' AND load_id IS NULL"),
        ),
        Index("ix_quotation_lines_load", "load_id"),
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
    cost_center: Mapped[str | None] = mapped_column(String(10))
    increase_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    # The date of the SKU's latest supplier price increase (2026-11-23).
    increase_on: Mapped[date | None] = mapped_column(Date)
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
    category: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    last_sale_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    last_sale_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    support_options: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="sin_marcar")
    reviewed_by: Mapped[str | None] = mapped_column(String(320))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    load_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("price_list_loads.id", ondelete="SET NULL")
    )
    # Stock of a main list's item in its list's warehouses, read by the sync (0 without stock,
    # NULL when it was not read). The board sorts by it. QuotationLine has no such field: only
    # the board reads and writes it, so an edit of the line keeps it.
    stock_on_hand: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    stock_available: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    # Unused (see RequestedProductModel): QuotationLine has no such field, so the
    # repository never writes it and new lines store NULL.
    requested_product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quotation_requested_products.id", ondelete="SET NULL"),
    )
