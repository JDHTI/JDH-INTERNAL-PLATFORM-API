"""Entities of the support settlement.

A settlement covers one calendar month (``YYYY-MM``). Its lines are the month's sales of the
references a supplier support covered on the sale's day; each one carries the supports that
applied (``SettledItem``) and the money the supplier owes for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

DRAFT = "borrador"
CLOSED = "cerrado"
STATUSES = (DRAFT, CLOSED)
STATUS_LABELS = {DRAFT: "Borrador", CLOSED: "Cerrado"}

# What happened to one support on one sale line. Only ``liquidado`` is claimed.
SETTLED = "liquidado"
IN_REVIEW = "en_revision"
TIERS = "escalas"
MINIMUM_NOT_MET = "minimo_no_cumplido"
ALREADY_DISCOUNTED = "descontado_en_factura"
NO_VALUE = "sin_valor"
ITEM_STATUSES = (SETTLED, IN_REVIEW, TIERS, MINIMUM_NOT_MET, ALREADY_DISCOUNTED, NO_VALUE)
ITEM_STATUS_LABELS = {
    SETTLED: "Liquidado",
    IN_REVIEW: "Apoyo en revisión",
    TIERS: "Apoyo con escalas: se liquida a mano",
    MINIMUM_NOT_MET: "No cumplió la cantidad mínima del mes",
    ALREADY_DISCOUNTED: "Ya descontado en la factura del proveedor",
    NO_VALUE: "No se pudo calcular",
}

# Marks of a sale line (several may apply).
SEVERAL_SUPPORTS = "varios_apoyos"
INVOICE_COST = "costo_factura"
PARTIAL_DISCOUNT = "descuento_parcial"
SALE_FLAGS = (SEVERAL_SUPPORTS, INVOICE_COST, PARTIAL_DISCOUNT)
SALE_FLAG_LABELS = {
    SEVERAL_SUPPORTS: "Varios apoyos distintos sumados",
    # Only in the quotation basis: the inventory basis always uses the invoice's cost.
    INVOICE_COST: "Sin cotización del proveedor: costo de la factura",
    PARTIAL_DISCOUNT: "El cliente recibió menos descuento del negociado",
}

QUOTE = "cotizacion"
INVOICE = "factura"

# The cost a month is settled with (2026-11-24): every month is calculated with both and each
# one is stored, read and closed on its own.
COST_QUOTE = "cotizacion"  # the supplier's SIESA quotation in force on the sale's day
COST_INVENTORY = "inventario"  # the inventory cost billed on the line (``consumo.ventas.costo``)
COST_BASES = (COST_QUOTE, COST_INVENTORY)
COST_BASIS_LABELS = {
    COST_QUOTE: "Costo cotización proveedor",
    COST_INVENTORY: "Costo inventario (facturado)",
}

BREAKDOWNS = ("regional", "business_unit", "salesperson", "customer")
# The ``supplier_code`` filter that asks for the lines and references with no supplier code.
NO_SUPPLIER = "__none__"


@dataclass(frozen=True)
class SaleLine:
    """One sale or return line of the month, as the DWH gives it (``consumo.ventas``).

    Quantities and values of a return are negative. ``cost`` and ``gross``/``net`` are line
    totals before VAT; ``quantity`` is in the inventory unit.
    """

    line_key: str
    sale_date: date
    sku: str
    quantity: Decimal
    gross: Decimal
    net: Decimal
    cost: Decimal
    document: str | None = None
    co: str | None = None
    co_name: str | None = None
    regional: str | None = None
    customer_key: str | None = None
    nit: str | None = None
    branch_id: str | None = None
    kg: Decimal | None = None
    warehouse_code: str | None = None
    warehouse_name: str | None = None
    is_return: bool = False
    reason: str | None = None
    salesperson: str | None = None


@dataclass(frozen=True)
class CustomerBranch:
    """A customer branch of the local master (``customers``, by ``cliente_key``)."""

    customer_key: str
    nit: str
    branch_id: str | None = None
    name: str | None = None
    channel: str | None = None
    business_unit: str | None = None
    billing_center: str | None = None
    billing_center_code: str | None = None
    sales_team_class: str | None = None
    business_group: str | None = None
    city: str | None = None


@dataclass(frozen=True)
class Product:
    """A product of the local master."""

    sku: str
    producto_key: str | None = None
    description: str | None = None
    supplier_code: str | None = None
    pack_factor: Decimal | None = None
    inventory_unit: str | None = None
    margins: dict[str, Decimal | None] = field(default_factory=dict)


@dataclass(frozen=True)
class Quote:
    """One activation of a supplier quotation (SIESA), per inventory unit or kilo."""

    sku: str
    since: date
    cost: Decimal
    unit: str = "UNID"
    until: date | None = None
    co: str | None = None


@dataclass(frozen=True)
class SettlementSupport:
    """The part of a supplier support the settlement needs, as stored today."""

    id: UUID
    version: int
    supplier_code: str | None
    kind: str
    calc_method: str
    status: str
    supplier_name: str | None = None
    customer_nit: str | None = None
    customer_branch_id: str | None = None
    customer_group: str | None = None
    group_wide: bool = False
    product_sku: str | None = None
    regionals: tuple[str, ...] = ()
    business_units: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    warehouse: str | None = None
    promo_warehouses: bool = False
    supplier_value: Decimal | None = None
    value_unit: str | None = None
    pack_factor: Decimal | None = None
    target_margin_pct: Decimal | None = None
    supported_margin_pct: Decimal | None = None
    supplier_min_margin_pct: Decimal | None = None
    min_quantity: Decimal | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    funding: str = "proveedor"
    settlement_method: str | None = None
    has_tiers: bool = False
    internal_request_id: UUID | None = None
    # The support a supplier-margin request's copy stands in for (it never adds to it).
    replaces_support_id: UUID | None = None


@dataclass(frozen=True)
class SettledItem:
    """One support applied to one sale line."""

    support_id: UUID
    support_version: int
    kind: str
    calc_method: str
    status: str
    supplier_value: Decimal | None
    value_unit: str | None
    valid_from: date | None
    valid_to: date | None
    unit_amount: Decimal
    amount: Decimal
    negotiated_pct: Decimal | None = None
    settled_pct: Decimal | None = None
    detail: str = ""


@dataclass(frozen=True)
class SettledSale:
    """A sale line with the supports that applied to it."""

    line: SaleLine
    supplier_code: str | None
    supplier_name: str | None
    description: str | None
    customer: CustomerBranch | None
    base_unit_cost: Decimal | None
    cost_source: str
    items: tuple[SettledItem, ...]
    flags: tuple[str, ...] = ()

    @property
    def amount(self) -> Decimal:
        """What the supplier owes for the line (only the settled supports)."""

        return sum((item.amount for item in self.items if item.status == SETTLED), Decimal(0))


@dataclass(frozen=True)
class ReferenceTotals:
    """Every sale of a supported reference in the month, settled or not."""

    supplier_code: str | None
    supplier_name: str | None
    sku: str
    description: str | None
    quantity: Decimal
    net: Decimal
    cost: Decimal
    amount: Decimal
    flagged_amount: Decimal
    supported_lines: int


@dataclass(frozen=True)
class SettlementResult:
    """A computed settlement, before it is stored."""

    sales: tuple[SettledSale, ...]
    references: tuple[ReferenceTotals, ...]
    sales_read: int


@dataclass(frozen=True)
class Settlement:
    """The header of a stored settlement."""

    id: UUID
    period: str
    status: str
    amount: Decimal
    flagged_amount: Decimal
    net: Decimal
    cost: Decimal
    sales_read: int
    supported_lines: int
    suppliers: int
    calculated_at: datetime
    calculated_by: str
    closed_at: datetime | None = None
    closed_by: str | None = None
    reopened_at: datetime | None = None
    reopened_by: str | None = None
    version: int = 1
    cost_basis: str = COST_QUOTE


__all__ = [
    "ALREADY_DISCOUNTED",
    "BREAKDOWNS",
    "CLOSED",
    "COST_BASES",
    "COST_BASIS_LABELS",
    "COST_INVENTORY",
    "COST_QUOTE",
    "DRAFT",
    "INVOICE",
    "INVOICE_COST",
    "IN_REVIEW",
    "ITEM_STATUSES",
    "ITEM_STATUS_LABELS",
    "MINIMUM_NOT_MET",
    "NO_SUPPLIER",
    "NO_VALUE",
    "PARTIAL_DISCOUNT",
    "QUOTE",
    "SALE_FLAGS",
    "SALE_FLAG_LABELS",
    "SETTLED",
    "SEVERAL_SUPPORTS",
    "STATUSES",
    "STATUS_LABELS",
    "TIERS",
    "CustomerBranch",
    "Product",
    "Quote",
    "ReferenceTotals",
    "SaleLine",
    "SettledItem",
    "SettledSale",
    "Settlement",
    "SettlementResult",
    "SettlementSupport",
]
