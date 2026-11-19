"""Entities of the supplier supports master."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.modules.commercial_masters.domain.margins import channel_field, margin_for


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
    # Applies also in the promotional warehouses (SIESA warehouse group 23); otherwise an order
    # from one of them goes at that warehouse's cost, without the support.
    promo_warehouses: bool = False
    supplier_value: Decimal | None = None
    jdh_pct: Decimal | None = None
    jdh_net_cost: Decimal | None = None
    value_unit: str | None = None
    target_margin_pct: Decimal | None = None
    min_margin_pct: Decimal | None = None
    list_margin_pct: Decimal | None = None
    # Margin the product may drop to on top of the supplier's support ("margen apoyado").
    supported_margin_pct: Decimal | None = None
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
    # Supports the load expires or voids with an observation: those its rows cross (rules
    # v2, ``on_overlap``) and those the supplier did not validate (``Validación = No``).
    # Items of ``overlap.Supersede``.
    retirements: tuple[Any, ...] = ()
    # The DWH cost of the supports of the file, saved in the same transaction (items of
    # ``SupportCost``).
    costs: tuple[Any, ...] = ()


@dataclass(frozen=True)
class LoadResult:
    """What a bulk load wrote."""

    load_id: UUID
    created: tuple[UUID, ...] = ()
    updated: tuple[UUID, ...] = ()
    expired: tuple[UUID, ...] = ()
    retired: tuple[UUID, ...] = ()


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
    # The internal support request it was born from: a bulk load never closes it.
    internal_request_id: UUID | None = None


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
    # The product's general margin (margin master) and the margin the supplier's money leaves
    # at the list price (cost at that margin).
    product_margin_pct: Decimal | None = None
    recomposed_list_margin: Decimal | None = None
    # How far the margin may drop on top of the support, the price it gives (net cost at that
    # margin) and the client's discount below the list price.
    supported_margin_pct: Decimal | None = None
    supported_price: Decimal | None = None
    supported_price_per: MoneyPair = field(default_factory=MoneyPair)
    # Disabled for now (always ``None``).
    customer_discount_pct: Decimal | None = None
    # The supported cost is above today's cost (or the value is negative): the support brings
    # no money at this cost and counts as 0.
    support_above_cost: bool = False


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
    # The product's optimal margins by list in the margin master (``margin_list``...).
    product_margins: Mapping[str, Decimal | None] = field(default_factory=dict)
    # Units per box in the product master (``None`` when the product is unknown).
    product_pack_factor: Decimal | None = None
    # The customer's margin list (``customer_margin_field``), when it has a NIT in the master.
    customer_margin_field: str | None = None
    # The customer's business group in the customer master (its name; the code is not known).
    customer_business_group: str | None = None

    @property
    def margin_field(self) -> str:
        """The margin list the support is priced on.

        The customer's when it has a NIT; else its channels (all Mayoristas channels →
        Mayoristas); else Food service, the default.
        """

        if self.customer_margin_field:
            return self.customer_margin_field
        channels = self.support.data.channels
        if channels and all(channel_field(channel) == "margin_wholesale" for channel in channels):
            return "margin_wholesale"
        return "margin_list"

    @property
    def product_margin(self) -> Decimal | None:
        """The product's optimal margin on :attr:`margin_field`."""

        return margin_for(self.product_margins, self.margin_field)


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
    # The support's group, else the customer's in the master; and the margin list it uses.
    business_group: str | None = None
    margin_list: str | None = None


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
