"""Entities of the supplier supports master."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class Issue:
    """A data problem attached to a support (see ``vocabulary.ISSUE_CODES``)."""

    code: str
    detail: str | None = None
    field: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON form stored in the ``issues`` column."""

        return {"code": self.code, "detail": self.detail, "field": self.field}


@dataclass(frozen=True)
class Tier:
    """A threshold of a support and the value it unlocks."""

    threshold_type: str
    from_value: Decimal
    to_value: Decimal | None
    value: Decimal


@dataclass(frozen=True)
class SupportData:
    """Everything a person (or the importer) can set on a support."""

    kind: str
    calc_method: str
    commercial_supplier_id: UUID | None = None
    funding: str = "proveedor"
    external_ref: str | None = None
    program: str | None = None
    settlement_method: str | None = None
    settlement_frequency: str | None = None
    customer_nit: str | None = None
    customer_branch_id: str | None = None
    customer_name: str | None = None
    customer_group: str | None = None
    product_id: UUID | None = None
    product_sku: str | None = None
    supplier_sku: str | None = None
    brand: str | None = None
    regionals: tuple[str, ...] = ()
    business_units: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    warehouse: str | None = None
    supplier_value: Decimal | None = None
    jdh_pct: Decimal | None = None
    jdh_net_cost: Decimal | None = None
    value_unit: str | None = None
    target_margin_pct: Decimal | None = None
    min_margin_pct: Decimal | None = None
    list_margin_pct: Decimal | None = None
    pack_factor: Decimal | None = None
    reference_cost: Decimal | None = None
    reference_list_price: Decimal | None = None
    reference_date: date | None = None
    min_quantity: Decimal | None = None
    committed_volume: Decimal | None = None
    budget_amount: Decimal | None = None
    budget_units: Decimal | None = None
    units_used: Decimal | None = None
    stock_quantity: Decimal | None = None
    conditions: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    end_condition: str | None = None
    authorized_by: str | None = None
    supplier_contact: str | None = None
    sales_rep: str | None = None
    notes: str | None = None
    source: str | None = None
    source_key: str | None = None
    status: str = "activo"
    is_enabled: bool = True
    unit_basis_source: str = "desconocido"
    special_case_id: UUID | None = None
    issues: tuple[Issue, ...] = ()
    tiers: tuple[Tier, ...] = ()


@dataclass(frozen=True)
class Trace:
    """What wrote a version of a support (``vocabulary.ORIGINS``) and the load, if any."""

    origin: str
    load_id: UUID | None = None


@dataclass(frozen=True)
class LoadPlan:
    """What a bulk load will write, decided before any write (all or nothing)."""

    load_id: UUID
    file_name: str
    file_sha256: str
    supplier_code: str | None
    sheet: str | None
    rows_read: int
    creates: tuple[tuple[UUID, SupportData], ...] = ()
    # (support id, version it was read at, new data)
    updates: tuple[tuple[UUID, int, SupportData], ...] = ()
    # (support id, version it was read at, last day in force)
    expirations: tuple[tuple[UUID, int, date], ...] = ()
    unchanged: int = 0
    missing: int = 0
    # Do not send it to the DWH before this moment (a big load waits for quiet hours).
    publish_not_before: datetime | None = None


@dataclass(frozen=True)
class LoadResult:
    """What a bulk load wrote."""

    load_id: UUID
    created: tuple[UUID, ...] = ()
    updated: tuple[UUID, ...] = ()
    expired: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class Support:
    """A stored support."""

    id: UUID
    data: SupportData
    void_reason: str | None
    voided_at: datetime | None
    created_by: str
    updated_by: str
    version: int
    created_at: datetime
    updated_at: datetime
    # What wrote this version (``vocabulary.ORIGINS``) and its bulk load, if any.
    last_origin: str | None = None
    last_load_id: UUID | None = None


@dataclass(frozen=True)
class MoneyPair:
    """A money value per box and per minimum selling unit."""

    box: Decimal | None = None
    unit: Decimal | None = None


@dataclass(frozen=True)
class Derived:
    """Amounts and margins computed from the stored inputs (never persisted)."""

    supplier_amount: Decimal | None = None
    jdh_amount: Decimal | None = None
    net_cost: Decimal | None = None
    pct_on_cost: Decimal | None = None
    pct_on_list: Decimal | None = None
    suggested_price: Decimal | None = None
    margin_order: Decimal | None = None
    margin_recomposed: Decimal | None = None
    min_margin_pct: Decimal | None = None
    # The same amounts per box and per unit, when the unit of the cost and the pack factor
    # are known; empty otherwise (a box of unknown size has no unit price).
    cost: MoneyPair = field(default_factory=MoneyPair)
    list_price: MoneyPair = field(default_factory=MoneyPair)
    supplier_amount_per: MoneyPair = field(default_factory=MoneyPair)
    net_cost_per: MoneyPair = field(default_factory=MoneyPair)
    suggested_price_per: MoneyPair = field(default_factory=MoneyPair)
    # Which cost the figures use: ``dwh`` (the quoter's rule) or ``referencia`` (the one
    # stored with the support); ``None`` when there is none.
    cost_basis: str | None = None


@dataclass(frozen=True)
class SupportCost:
    """The cost of a support's SKU in the DWH with the quoter's rule.

    The highest of the supplier's quotation in force and the inventory average cost, per
    inventory unit, rounded up, without the price lists' factor; for a client with several
    branches, the highest of their lists' warehouses; without a client, the highest among the
    list 1 warehouses.
    """

    support_id: UUID
    product_sku: str
    plan_key: str
    plan_label: str
    unit_cost: Decimal | None
    source: str | None
    quote_cost: Decimal | None
    inventory_cost: Decimal | None
    snapshot_date: date | None
    computed_at: datetime | None = None


@dataclass(frozen=True)
class SupportRow:
    """A stored support with the names joined from the masters."""

    support: Support
    supplier_code: str | None = None
    supplier_name: str | None = None
    product_description: str | None = None
    product_brand: str | None = None
    conflicts: tuple[UUID, ...] = ()
    attachment_count: int = 0
    dwh_cost: SupportCost | None = None


@dataclass(frozen=True)
class SupportView:
    """A support with resolved names, validity, conflicts and derived values."""

    support: Support
    supplier_code: str | None
    supplier_name: str | None
    product_description: str | None
    product_brand: str | None
    validity: str
    state: str
    needs_review: bool
    has_conflicts: bool
    derived: Derived
    conflicts: tuple[UUID, ...] = ()
    attachment_count: int = 0
    dwh_cost: SupportCost | None = None


@dataclass(frozen=True)
class SupportPage:
    """A page of supports."""

    items: list[SupportView]
    total: int
    page: int
    size: int


@dataclass(frozen=True)
class Conflict:
    """Another support in force at the same time over the same scope."""

    id: UUID
    valid_from: date | None
    valid_to: date | None
    program: str | None
    supplier_value: Decimal | None
    calc_method: str
    status: str


@dataclass(frozen=True)
class Checked:
    """A support after the strict checks: its normalized data, or why it was refused."""

    data: SupportData | None = None
    error: str | None = None
    # The field the error belongs to, when it is one.
    field: str | None = None


@dataclass(frozen=True)
class ImportOutcome:
    """Result of importing one support."""

    created: bool
    support_id: UUID | None
    issues: list[Issue] = field(default_factory=list)
    updated: bool = False
    skipped_manual: bool = False
