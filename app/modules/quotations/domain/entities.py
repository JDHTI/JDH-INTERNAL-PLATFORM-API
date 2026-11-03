"""Entities of the quoter.

Prices and costs are per **inventory unit** and before VAT; margins, variations and points
are fractions (0.18 = 18 %); ``vat_pct`` is in points (19 = 19 %), as the masters keep it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


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


@dataclass(frozen=True)
class CustomerContext:
    """What a quotation needs from the customer, merged across its branches."""

    nit: str
    name: str | None
    channel_code: str | None
    channel: str | None
    price_list_id: str
    price_list_name: str | None
    price_list_customers: int | None
    billing_center_code: str | None
    salesperson_code: str | None
    salesperson_name: str | None
    warnings: tuple[str, ...] = ()


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
    # ``historico`` (from the sales window) or ``agregado`` (added by the advisor).
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
    price_list_id: str
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


@dataclass(frozen=True)
class QuotationDetail:
    """A quotation with its lines, in position order."""

    quotation: Quotation
    lines: tuple[QuotationLine, ...]


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
class TemplateRow:
    """One row of the quotation workbook the advisor uploads back.

    ``new_price`` and ``new_margin_pct`` are what the cells say (``None`` when blank);
    ``problem`` is set when a cell could not be read.
    """

    row_number: int
    sku: str
    new_price: Decimal | None = None
    new_margin_pct: Decimal | None = None
    problem: str | None = None


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
