"""Entities of the quoter.

Prices and costs are per **inventory unit** and before VAT; margins, variations and points
are fractions (0.18 = 18 %); ``vat_pct`` is in points (19 = 19 %), as the masters keep it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.modules.quotations.domain import vocabulary


@dataclass(frozen=True)
class QuotationPolicy:
    """The general rule: how far below its target margin a new price may leave a line."""

    max_points_below_target: Decimal = Decimal("0.01")


@dataclass(frozen=True)
class Branch:
    """One branch of a customer in the warehouse (``consumo.clientes``)."""

    branch_id: str | None
    legal_name: str | None
    channel_code: str | None
    channel: str | None
    price_list_id: str | None
    price_list_name: str | None
    billing_center_code: str | None
    salesperson_code: str | None
    salesperson_name: str | None
    is_active: bool
    # Filled when the branches of a price list are read (a list update): its customer and key.
    nit: str | None = None
    customer_key: str | None = None


@dataclass(frozen=True)
class PriceListInfo:
    """A SIESA price list (``consumo.listas_precios``), as it is chosen for a list update."""

    code: str
    name: str | None
    is_active: bool
    # Active branches assigned to it (SIESA counts branches, not NITs).
    active_branches: int | None = None


@dataclass(frozen=True)
class ListItem:
    """A SKU with a price in force on a list, per inventory unit, with its item data.

    ``price`` is ``None`` when SIESA keeps a placeholder ($1 or less) or only a unit with no
    factor: the line then has no current price to compare.
    """

    item: ItemInfo
    price: Decimal | None
    value_tax_amount: Decimal | None = None
    placeholder: bool = False


@dataclass(frozen=True)
class CustomerContext:
    """What a quotation needs from the customer, merged across its branches."""

    nit: str
    name: str | None
    channel_code: str | None
    channel: str | None
    # ``None`` for a temporary customer: it has no list in SIESA.
    price_list_id: str | None
    price_list_name: str | None
    price_list_customers: int | None
    billing_center_code: str | None
    salesperson_code: str | None
    salesperson_name: str | None
    warnings: tuple[str, ...] = ()
    # Operation centers the lines are costed from; none is *Nacional* (every warehouse).
    cost_centers: tuple[str, ...] = ()


@dataclass(frozen=True)
class LastSale:
    """The last sale of one SKU to the customer within the window."""

    sku: str
    sale_date: date
    document: str | None
    sale_unit: str | None
    quantity: Decimal
    gross_price: Decimal
    net_price: Decimal
    discount_pct: Decimal | None
    sales_in_window: int
    description: str | None = None
    unit: str | None = None
    vat_pct: Decimal | None = None
    supplier_code: str | None = None
    pack_factor: Decimal | None = None
    # State of the item in SIESA today (``consumo.productos.estado``).
    is_active: bool = True
    # ``consumo.productos.categoria``.
    category: str | None = None
    # Billing operation center of the sale (``consumo.ventas.co``).
    center_code: str | None = None
    # Cost of goods sold per inventory unit on that sale; read only by list updates.
    unit_cost: Decimal | None = None


@dataclass(frozen=True)
class BillingCenter:
    """An operation center a quotation can be billed and priced from."""

    code: str
    name: str | None


@dataclass(frozen=True)
class TemporaryCustomer:
    """A customer that is not in SIESA yet, quoted with local data only."""

    name: str
    legal_name: str
    trade_name: str | None = None
    nit: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "legal_name": self.legal_name,
            "trade_name": self.trade_name,
            "nit": self.nit,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TemporaryCustomer:
        return cls(
            name=data["name"],
            legal_name=data["legal_name"],
            trade_name=data.get("trade_name"),
            nit=data.get("nit"),
        )


@dataclass(frozen=True)
class ItemInfo:
    """A warehouse item (``consumo.productos``), for a SKU the customer has not bought."""

    sku: str
    description: str | None = None
    unit: str | None = None
    vat_pct: Decimal | None = None
    supplier_code: str | None = None
    pack_factor: Decimal | None = None
    is_active: bool = True
    category: str | None = None


@dataclass(frozen=True)
class CostInfo:
    """The two costs of a SKU the rule compares, as the warehouse has them today.

    ``quote_cost`` is the highest supplier quotation in force (gross cost with ICUI and IBUA);
    ``discarded_quote`` the highest one left out for being far above the last cost.
    ``inventory_cost`` is the average cost of the list's warehouse (rounded to pesos).
    """

    sku: str
    quote_cost: Decimal | None = None
    discarded_quote: Decimal | None = None
    inventory_cost: Decimal | None = None
    stock: Decimal | None = None
    product_line: str | None = None
    is_active: bool = True
    snapshot_date: date | None = None
    # The operation center whose warehouses gave this cost, when the quotation names some.
    center_code: str | None = None


@dataclass(frozen=True)
class ListPrice:
    """Price in force of a SKU in a price list, per inventory unit."""

    sku: str
    price: Decimal | None
    value_tax_amount: Decimal | None = None


@dataclass(frozen=True)
class ValueTaxKey:
    """A tax charged as a value (impoconsumo, liquor), not a percentage."""

    sku: str
    key: str
    name: str | None


@dataclass(frozen=True)
class ProductMaster:
    """The platform's copy of a product: description, units, VAT, supplier and margins."""

    sku: str
    description: str | None = None
    unit: str | None = None
    vat_pct: Decimal | None = None
    pack_factor: Decimal | None = None
    supplier_code: str | None = None
    margins: dict[str, Decimal | None] = field(default_factory=dict)
    # Liquor tax per unit by regional (margin master), when the price list has no value.
    liquor_tax: Decimal | None = None
    liquor_tax_coast: Decimal | None = None


@dataclass(frozen=True)
class Seller:
    """A warehouse salesperson whose e-mail is the user's."""

    code: str
    name: str | None
    email: str | None


@dataclass(frozen=True)
class SellerContact:
    """The customer's salesperson as the offer shows them (``consumo.vendedores``)."""

    code: str
    name: str | None
    email: str | None
    phone: str | None

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form, for storage."""

        return {"code": self.code, "name": self.name, "email": self.email, "phone": self.phone}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SellerContact:
        """Inverse of :meth:`as_dict`."""

        return cls(
            code=data["code"],
            name=data.get("name"),
            email=data.get("email"),
            phone=data.get("phone"),
        )


@dataclass(frozen=True)
class AppliedSupport:
    """The support a line is priced with."""

    support_id: UUID
    kind: str
    calc_method: str
    program: str | None
    amount: Decimal | None
    share_of_cost: Decimal | None
    required_margin_pct: Decimal | None
    detail: str
    conditions: str | None = None
    needs_review: bool = False
    valid_from: date | None = None
    valid_to: date | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form, for storage."""

        return {
            "support_id": str(self.support_id),
            "kind": self.kind,
            "calc_method": self.calc_method,
            "program": self.program,
            "amount": _text(self.amount),
            "share_of_cost": _text(self.share_of_cost),
            "required_margin_pct": _text(self.required_margin_pct),
            "detail": self.detail,
            "conditions": self.conditions,
            "needs_review": self.needs_review,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppliedSupport:
        """Inverse of :meth:`as_dict`."""

        return cls(
            support_id=UUID(data["support_id"]),
            kind=data["kind"],
            calc_method=data["calc_method"],
            program=data.get("program"),
            amount=_decimal(data.get("amount")),
            share_of_cost=_decimal(data.get("share_of_cost")),
            required_margin_pct=_decimal(data.get("required_margin_pct")),
            detail=data.get("detail") or "",
            conditions=data.get("conditions"),
            needs_review=bool(data.get("needs_review")),
            valid_from=date.fromisoformat(data["valid_from"]) if data.get("valid_from") else None,
            valid_to=date.fromisoformat(data["valid_to"]) if data.get("valid_to") else None,
        )


@dataclass(frozen=True)
class SupportCandidate:
    """A support that would cover the line and was not applied, with the reason."""

    support_id: UUID
    calc_method: str
    program: str | None
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "support_id": str(self.support_id),
            "calc_method": self.calc_method,
            "program": self.program,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SupportCandidate:
        return cls(
            support_id=UUID(data["support_id"]),
            calc_method=data["calc_method"],
            program=data.get("program"),
            reason=data.get("reason") or "",
        )


@dataclass(frozen=True)
class SupportOption:
    """A support that covers a list update's line, with the prices it would leave.

    A list update shows every support of the list's customers on the SKU so the person sees
    what each one allows: ``min_price`` (the rule's floor with that support) and
    ``optimal_price`` (its target margin). The one with the lowest minimum is applied.
    ``reason`` says why one cannot be applied (in review, one warehouse, no value...).
    """

    support_id: UUID
    kind: str
    calc_method: str
    program: str | None
    detail: str
    amount: Decimal | None = None
    required_margin_pct: Decimal | None = None
    min_price: Decimal | None = None
    optimal_price: Decimal | None = None
    applied: bool = False
    reason: str | None = None
    customer_nit: str | None = None
    valid_to: date | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "support_id": str(self.support_id),
            "kind": self.kind,
            "calc_method": self.calc_method,
            "program": self.program,
            "detail": self.detail,
            "amount": _text(self.amount),
            "required_margin_pct": _text(self.required_margin_pct),
            "min_price": _text(self.min_price),
            "optimal_price": _text(self.optimal_price),
            "applied": self.applied,
            "reason": self.reason,
            "customer_nit": self.customer_nit,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SupportOption:
        return cls(
            support_id=UUID(data["support_id"]),
            kind=data.get("kind") or "",
            calc_method=data["calc_method"],
            program=data.get("program"),
            detail=data.get("detail") or "",
            amount=_decimal(data.get("amount")),
            required_margin_pct=_decimal(data.get("required_margin_pct")),
            min_price=_decimal(data.get("min_price")),
            optimal_price=_decimal(data.get("optimal_price")),
            applied=bool(data.get("applied")),
            reason=data.get("reason"),
            customer_nit=data.get("customer_nit"),
            valid_to=date.fromisoformat(data["valid_to"]) if data.get("valid_to") else None,
        )


@dataclass(frozen=True)
class QuotationLine:
    """One SKU of a quotation: the snapshot it was built with and the advisor's price."""

    id: UUID
    position: int
    sku: str
    description: str | None
    unit: str | None
    pack_factor: Decimal | None
    supplier_code: str | None
    supplier_name: str | None
    # ``None`` for a SKU the advisor added: the customer has not bought it in the window.
    last_price: Decimal | None
    last_gross_price: Decimal | None
    last_discount_pct: Decimal | None
    last_sale_date: date | None
    last_document: str | None
    last_sale_unit: str | None
    last_quantity: Decimal | None
    sales_in_window: int
    list_price: Decimal | None
    vat_pct: Decimal | None
    value_tax_key: str | None
    value_tax_name: str | None
    value_tax_amount: Decimal | None
    # The cost the rule uses: the highest of quotation and inventory, times the list factor.
    cost: Decimal | None
    target_margin_pct: Decimal | None
    margin_basis: str | None
    min_margin_pct: Decimal | None
    current_margin_pct: Decimal | None
    current_margin_with_support_pct: Decimal | None
    min_price: Decimal | None
    support: AppliedSupport | None = None
    support_candidates: tuple[SupportCandidate, ...] = ()
    new_price: Decimal | None = None
    new_margin_pct: Decimal | None = None
    new_margin_with_support_pct: Decimal | None = None
    variation_pct: Decimal | None = None
    compliance: str | None = None
    problems: tuple[str, ...] = ()
    # ``historico`` (from the sales window), ``agregado`` (added by the advisor) or ``local``
    # (a product only for the quotation: not in SIESA, no cost, never exported).
    origin: str = "historico"
    quote_cost: Decimal | None = None
    discarded_quote_cost: Decimal | None = None
    inventory_cost: Decimal | None = None
    # ``cotizacion`` or ``inventario``: which one set ``cost``.
    cost_source: str | None = None
    # Price at the target margin (with the support), rounded as the list family does.
    optimal_price: Decimal | None = None
    # The last price leaves the margin (with support) under the minimum: review and update.
    below_target: bool = False
    # Inactive in SIESA: shown to be replaced, never exported.
    is_active: bool = True
    # Product category in SIESA (``consumo.productos.categoria``); ``None`` when unknown.
    category: str | None = None
    # Inventory units the customer would buy (up to two decimals), for the offer's totals.
    quantity: Decimal | None = None
    # The operation center whose warehouses set ``cost``; ``None`` for *Nacional* or a list
    # with its own warehouses.
    cost_center: str | None = None
    # New cost of the product's latest price increase when SIESA does not have it yet (no
    # system update date): the third value the cost compares.
    increase_cost: Decimal | None = None
    # A list update compares the list price (``last_price``), so the SKU's last sale to the
    # list's customers goes here: its net price and unit cost of goods sold.
    last_sale_price: Decimal | None = None
    last_sale_cost: Decimal | None = None
    # A list update: every support of the list's customers on the SKU, with its prices.
    support_options: tuple[SupportOption, ...] = ()
    # A list update's review of the item (``sin_marcar``, ``por_validar``, ``validada``), who
    # set it last and when.
    review_status: str = vocabulary.UNMARKED
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    # The person's observation on the item.
    note: str | None = None
    # The load that took the validated price to SIESA; ``None`` while it waits in the staging.
    load_id: UUID | None = None

    @property
    def is_staged(self) -> bool:
        """Validated with a price and not loaded yet: it waits in its list's staging."""

        return (
            self.review_status == vocabulary.VALIDATED
            and self.load_id is None
            and self.new_price is not None
        )

    @property
    def is_local(self) -> bool:
        """A product only for the quotation: SIESA does not have it."""

        return self.origin == vocabulary.LOCAL

    @property
    def support_amount(self) -> Decimal:
        """Money of the support per unit (0 without one)."""

        if self.support is None or self.support.amount is None:
            return Decimal(0)
        return self.support.amount


@dataclass(frozen=True)
class Quotation:
    """The header of a quotation: the customer as it was, the window and the state."""

    id: UUID
    customer_nit: str
    customer_name: str | None
    channel_code: str | None
    channel: str | None
    # ``None`` for a temporary customer.
    price_list_id: str | None
    price_list_name: str | None
    price_list_customers: int | None
    billing_center_code: str | None
    salesperson_code: str | None
    salesperson_name: str | None
    window_months: int
    status: str
    activation_date: date | None
    # The list the files write to: the customer's (update) or a new one (creation).
    export_price_list_id: str | None
    warnings: tuple[str, ...]
    built_at: datetime
    cost_cut_date: date | None
    max_points_below_target: Decimal
    line_count: int
    changed_count: int
    below_rule_count: int
    version: int
    created_by_id: UUID
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime
    list_action: str = "actualizacion"
    new_list_name: str | None = None
    # ``nacional`` or ``costa`` (billing operation center 004): which liquor tax applies.
    regional: str = "nacional"
    price_family: str = "generales"
    stock_source: str | None = None
    below_target_count: int = 0
    confirmed_at: datetime | None = None
    confirmed_by: str | None = None
    validated_at: datetime | None = None
    validated_by: str | None = None
    # Why the approver sent it back, or the advisor withdrew it.
    review_note: str | None = None
    # The offer holds ``validity_days`` from ``valid_from`` (the day it was set, Bogotá).
    validity_days: int = vocabulary.DEFAULT_VALIDITY_DAYS
    valid_from: date | None = None
    # Conditions printed on the customer offer, in order.
    conditions: tuple[str, ...] = ()
    include_seller: bool = True
    quote_quantities: bool = False
    # ``changed`` or ``all``: which lines the offer carries.
    offer_scope: str = vocabulary.OFFER_CHANGED
    # The customer's salesperson when the quotation was built or refreshed.
    seller_contact: SellerContact | None = None
    # Operation centers the lines are costed from; none is *Nacional* (every warehouse).
    cost_centers: tuple[str, ...] = ()
    # Where ``cost_centers`` comes from: ``auto`` (the customer's branches) or ``manual``
    # (chosen; a refresh keeps it).
    billing_center_source: str = vocabulary.CENTER_AUTO
    billing_center_name: str | None = None
    # Who marked it loaded in SIESA, and when.
    uploaded_at: datetime | None = None
    uploaded_by: str | None = None
    # A customer not in SIESA yet: no sales, no list, priced from the chosen center.
    temporary_customer: TemporaryCustomer | None = None
    # The offer shows the variation of each price against the last one.
    include_variation: bool = False
    # False when it started empty: a refresh prices the added SKUs and reads no sales.
    with_history: bool = True
    # ``cotizacion`` or ``lista`` (the update of an existing price list).
    kind: str = vocabulary.QUOTATION
    # A list update: ``cliente`` (started from a customer's list) or ``lista``.
    source: str | None = None

    @property
    def is_temporary_customer(self) -> bool:
        return self.temporary_customer is not None

    @property
    def is_list_update(self) -> bool:
        """The update of an existing list (``lista`` or ``principal``)."""

        return self.kind in vocabulary.LIST_KINDS

    @property
    def valid_until(self) -> date | None:
        """Last day the offer holds: ``valid_from + validity_days``."""

        if self.valid_from is None:
            return None
        return self.valid_from + timedelta(days=self.validity_days)


@dataclass(frozen=True)
class QuotationDetail:
    """A quotation with its lines, in position order."""

    quotation: Quotation
    lines: tuple[QuotationLine, ...]


@dataclass(frozen=True)
class StagedItem:
    """A validated item waiting in its list's staging, with the update it belongs to."""

    quotation_id: UUID
    quotation_version: int
    # The customer's name for an update started from a customer, else the list's name.
    quotation_name: str | None
    price_list_id: str
    price_list_name: str | None
    line: QuotationLine


@dataclass(frozen=True)
class PriceListLoad:
    """Prices of one list's staging loaded in SIESA: what went and who loaded it."""

    id: UUID
    kind: str
    price_list_id: str
    price_list_name: str | None
    activation_date: date
    line_count: int
    # ``{"sku", "unit", "price", "tax_key", "tax_amount", "quotation_id", "line_id"}`` each.
    rows: tuple[dict[str, Any], ...]
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class StockRow:
    """Stock of a SKU in one warehouse (``consumo.inventario_fecha``, the morning's photo)."""

    sku: str
    warehouse: str
    warehouse_name: str | None
    center_code: str | None
    on_hand: Decimal
    available: Decimal | None
    snapshot_at: datetime | None = None


@dataclass(frozen=True)
class PriceVersion:
    """A version of a SKU's price on a list, per inventory unit (``precios_lista_historico``);
    ``valid_to`` is ``None`` for the one in force."""

    valid_from: date
    valid_to: date | None
    price: Decimal
    value_tax_amount: Decimal | None = None


@dataclass(frozen=True)
class MonthlySale:
    """What a SKU sold for in one month: net price per inventory unit and quantity."""

    month: date
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class LineInsight:
    """What an item's detail shows beyond the line: stock by warehouse, the list price's
    history and the monthly sale price to the list's customers.

    ``sales_read`` is false when the warehouse did not answer the sales in time.
    """

    sku: str
    warehouses: tuple[str, ...]
    stock: tuple[StockRow, ...]
    history: tuple[PriceVersion, ...]
    sales: tuple[MonthlySale, ...]
    sales_scope: str
    sales_read: bool = True


@dataclass(frozen=True)
class QuotationPage:
    items: tuple[Quotation, ...]
    total: int
    page: int
    size: int


@dataclass(frozen=True)
class CustomerOption:
    """A customer to quote, merged across its branches (local master copy)."""

    nit: str
    name: str | None
    channel_code: str | None
    channel: str | None
    salesperson_code: str | None
    salesperson_name: str | None
    branch_count: int
    active_branch_count: int

    @property
    def is_active(self) -> bool:
        return self.active_branch_count > 0


@dataclass(frozen=True)
class CustomerPage:
    items: tuple[CustomerOption, ...]
    total: int
    page: int
    size: int


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))
