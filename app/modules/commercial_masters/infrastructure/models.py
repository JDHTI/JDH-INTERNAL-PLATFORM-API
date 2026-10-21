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
    list_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    list_margin_notes: Mapped[str | None] = mapped_column(Text)
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
