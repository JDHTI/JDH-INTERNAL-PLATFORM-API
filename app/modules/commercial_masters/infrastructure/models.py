"""SQLAlchemy models for the commercial masters mirrored from the data warehouse."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
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
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class CommercialSupplierModel(Base):
    """Commercial supplier: the ERP "criterion plan 2" label that groups products.

    ``code`` is the same code space as the ``Cod. Proveedor`` of the supports
    workbook. It is not a tax identity; see ``CommercialSupplierNitModel``.
    """

    __tablename__ = "commercial_suppliers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    product_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    active_product_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CommercialSupplierNitModel(Base):
    """A legal NIT that a commercial supplier is bought from.

    Suggested from purchase history (the NIT with most linked products is the
    primary one) and confirmed by hand.
    """

    __tablename__ = "commercial_supplier_nits"
    __table_args__ = (
        UniqueConstraint("commercial_supplier_id", "nit", name="uq_commercial_supplier_nits_pair"),
        Index("ix_commercial_supplier_nits_nit", "nit"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    commercial_supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("commercial_suppliers.id", ondelete="CASCADE"),
        nullable=False,
    )
    nit: Mapped[str] = mapped_column(Text, nullable=False)
    product_links: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SupplierModel(Base):
    """Legal supplier by branch (``consumo.proveedores``). Bank data is never copied."""

    __tablename__ = "suppliers"
    __table_args__ = (
        Index("ix_suppliers_nit", "nit"),
        Index("ix_suppliers_is_active", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    proveedor_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    company_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    nit: Mapped[str] = mapped_column(Text, nullable=False)
    branch_id: Mapped[str | None] = mapped_column(Text)
    legal_name: Mapped[str | None] = mapped_column(Text)
    branch_name: Mapped[str | None] = mapped_column(Text)
    establishment_name: Mapped[str | None] = mapped_column(Text)
    supplier_class: Mapped[str | None] = mapped_column(Text)
    supplier_type: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    payment_condition: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text)
    branch_state: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    entry_date: Mapped[date | None] = mapped_column(Date)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CustomerModel(Base):
    """Client by branch (``consumo.clientes``)."""

    __tablename__ = "customers"
    __table_args__ = (
        Index("ix_customers_nit", "nit"),
        Index("ix_customers_channel", "channel"),
        Index("ix_customers_business_unit", "business_unit"),
        Index("ix_customers_business_group", "business_group"),
        Index("ix_customers_is_active", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    cliente_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    company_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    nit: Mapped[str] = mapped_column(Text, nullable=False)
    branch_id: Mapped[str | None] = mapped_column(Text)
    legal_name: Mapped[str | None] = mapped_column(Text)
    branch_name: Mapped[str | None] = mapped_column(Text)
    establishment_name: Mapped[str | None] = mapped_column(Text)
    channel_code: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(Text)
    business_unit_code: Mapped[str | None] = mapped_column(Text)
    business_unit: Mapped[str | None] = mapped_column(Text)
    business_group_code: Mapped[str | None] = mapped_column(Text)
    business_group: Mapped[str | None] = mapped_column(Text)
    billing_center_code: Mapped[str | None] = mapped_column(Text)
    billing_center: Mapped[str | None] = mapped_column(Text)
    logistics_zone: Mapped[str | None] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    salesperson_code: Mapped[str | None] = mapped_column(Text)
    salesperson_name: Mapped[str | None] = mapped_column(Text)
    # The seller's team in ``consumo.vendedores`` (``equipo``, ``clase_equipo``).
    sales_team: Mapped[str | None] = mapped_column(Text)
    sales_team_class: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text)
    branch_state: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProductModel(Base):
    """Product (``consumo.productos``); ``sku`` is the JDH reference."""

    __tablename__ = "products"
    __table_args__ = (
        Index("ix_products_commercial_supplier_code", "commercial_supplier_code"),
        Index("ix_products_brand", "brand"),
        Index("ix_products_category", "category"),
        Index("ix_products_is_active", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    producto_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    sku: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    supplier_sku: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    commercial_supplier_code: Mapped[str | None] = mapped_column(Text)
    manufacturer: Mapped[str | None] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    subcategory: Mapped[str | None] = mapped_column(Text)
    product_line: Mapped[str | None] = mapped_column(Text)
    product_subline: Mapped[str | None] = mapped_column(Text)
    product_group: Mapped[str | None] = mapped_column(Text)
    pack_factor: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    inventory_unit: Mapped[str | None] = mapped_column(Text)
    sales_vat_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    main_barcode: Mapped[str | None] = mapped_column(Text)
    # Current average cost of the minimum selling unit, from the warehouse inventory.
    avg_unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    avg_unit_cost_date: Mapped[date | None] = mapped_column(Date)
    # Margins by channel, loaded from the margin master and edited by hand. The warehouse
    # sync never writes them. ``None`` means "no margin defined" (the master writes 0).
    margin_list: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    margin_wholesale: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    margin_mass_food: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    margin_market: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    margin_salsamentarias: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    margin_notes: Mapped[str | None] = mapped_column(Text)
    margin_source: Mapped[str | None] = mapped_column(String(20))
    margin_updated_by: Mapped[str | None] = mapped_column(String(320))
    margin_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Liquor consumption tax per unit (``IMPTOLIC``); orders may be keyed without it.
    liquor_tax: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    liquor_tax_coast: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    liquor_tax_group: Mapped[str | None] = mapped_column(String(20))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProductSupplierModel(Base):
    """Products bought from a legal supplier, derived from purchase history."""

    __tablename__ = "product_suppliers"
    __table_args__ = (
        UniqueConstraint("producto_key", "proveedor_key", name="uq_product_suppliers_pair"),
        Index("ix_product_suppliers_proveedor_key", "proveedor_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    producto_key: Mapped[str] = mapped_column(Text, nullable=False)
    proveedor_key: Mapped[str] = mapped_column(Text, nullable=False)
    first_purchase: Mapped[date | None] = mapped_column(Date)
    last_purchase: Mapped[date | None] = mapped_column(Date)
    purchase_lines: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SupplierProductCodeModel(Base):
    """A supplier's own SKU for a JDH product, with its pack factor."""

    __tablename__ = "supplier_product_codes"
    __table_args__ = (
        UniqueConstraint(
            "commercial_supplier_id",
            "supplier_sku",
            "product_id",
            name="uq_supplier_product_codes_triple",
        ),
        Index("ix_supplier_product_codes_product_id", "product_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    commercial_supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("commercial_suppliers.id", ondelete="CASCADE"),
        nullable=False,
    )
    supplier_sku: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    pack_factor: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    kg_per_unit: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PriceIncreaseModel(Base):
    """One cost change a supplier notified for a reference (``Acumulado Alza Precios``).

    ``row_key`` (reference, increase date, notification date) is what an upload upserts on.
    No foreign key to ``products``: the history keeps references SIESA no longer has. The
    product's own data is read from ``products`` and never stored here.
    """

    __tablename__ = "product_price_increases"
    __table_args__ = (
        Index("ix_product_price_increases_sku", "sku", "increased_on"),
        # The analysis reads a period by when the increase takes effect.
        Index("ix_product_price_increases_increased_on", "increased_on"),
        Index("ix_product_price_increases_status", "status"),
        Index(
            "ix_product_price_increases_pending",
            "sku",
            postgresql_where="system_updated_on IS NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    row_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    notified_on: Mapped[date | None] = mapped_column(Date)
    system_updated_on: Mapped[date | None] = mapped_column(Date)
    increased_on: Mapped[date | None] = mapped_column(Date)
    supplier_sku: Mapped[str | None] = mapped_column(Text)
    ean: Mapped[str | None] = mapped_column(Text)
    regional: Mapped[str | None] = mapped_column(Text)
    icui_pct: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    ibua: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    old_cost_with_tax: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    old_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    new_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    new_cost_with_tax: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    variation_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    variation_pct: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    status: Mapped[str | None] = mapped_column(String(10))
    observations: Mapped[str | None] = mapped_column(Text)
    margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    siesa_changed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by: Mapped[str] = mapped_column(String(320), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
