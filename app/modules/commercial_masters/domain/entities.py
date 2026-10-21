"""Entities of the commercial masters (suppliers, customers, products)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

_NON_DIGITS = re.compile(r"\D+")


def clean_nit(value: object) -> str | None:
    """Digits of a NIT with no check digit separator, or ``None`` when blank.

    The warehouse stores NITs without verification digit; workbook cells carry
    spaces, NBSP, dots, dashes, floats (``800116299.0``) and sometimes the check
    digit. Only digits are kept; callers decide about the check digit.
    """

    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    digits = _NON_DIGITS.sub("", text)
    return digits or None


@dataclass(frozen=True)
class Page[T]:
    """One page of results."""

    items: list[T]
    total: int
    page: int
    size: int


@dataclass(frozen=True)
class CommercialSupplierNit:
    """A legal NIT a commercial supplier is bought from."""

    nit: str
    product_links: int
    is_primary: bool
    confirmed: bool


@dataclass(frozen=True)
class CommercialSupplier:
    """ERP supplier code that groups products (``Cod. Proveedor``)."""

    id: UUID
    code: str
    name: str
    is_active: bool
    product_count: int
    active_product_count: int
    list_margin_pct: Decimal | None
    list_margin_notes: str | None
    synced_at: datetime | None
    nits: list[CommercialSupplierNit] = field(default_factory=list)


@dataclass(frozen=True)
class LegalSupplier:
    """Legal supplier by branch."""

    id: UUID
    nit: str
    branch_id: str | None
    legal_name: str | None
    branch_name: str | None
    supplier_class: str | None
    supplier_type: str | None
    city: str | None
    payment_condition: str | None
    state: str | None
    branch_state: str | None
    is_active: bool


@dataclass(frozen=True)
class Customer:
    """Client by branch."""

    id: UUID
    nit: str
    branch_id: str | None
    legal_name: str | None
    branch_name: str | None
    establishment_name: str | None
    channel: str | None
    business_unit: str | None
    business_group: str | None
    billing_center: str | None
    logistics_zone: str | None
    department: str | None
    city: str | None
    salesperson_name: str | None
    state: str | None
    branch_state: str | None
    is_active: bool


@dataclass(frozen=True)
class Product:
    """Product; ``sku`` is the JDH reference."""

    id: UUID
    sku: str
    supplier_sku: str | None
    description: str | None
    state: str | None
    is_active: bool
    commercial_supplier_code: str | None
    manufacturer: str | None
    brand: str | None
    category: str | None
    subcategory: str | None
    product_line: str | None
    pack_factor: Decimal | None
    inventory_unit: str | None
    main_barcode: str | None


@dataclass(frozen=True)
class ProductRef:
    """The few product fields a support needs to link and display."""

    id: UUID
    sku: str
    description: str | None
    brand: str | None
    commercial_supplier_code: str | None
    is_active: bool
    pack_factor: Decimal | None = None
    avg_unit_cost: Decimal | None = None


@dataclass(frozen=True)
class SupplierProductCode:
    """A supplier's own SKU for a JDH product."""

    commercial_supplier_code: str
    supplier_sku: str
    product_sku: str
    pack_factor: Decimal | None = None
    kg_per_unit: Decimal | None = None
    notes: str | None = None


@dataclass(frozen=True)
class SyncReport:
    """Counts of one master sync."""

    master: str
    read: int
    upserted: int
    deactivated: int
    started_at: datetime
    finished_at: datetime
    extra: dict[str, int] = field(default_factory=dict)
