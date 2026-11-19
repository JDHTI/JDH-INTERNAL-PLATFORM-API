"""The offer the customer receives: which lines it carries, at what price, and its totals.

The API and the customer workbook both read from here, so they never disagree::

    offer price     = new price, else last price (a local product: its new price)
    line base       = round(quantity x offer price)
    line VAT        = round(base x VAT / 100)
    line liquor tax = round(quantity x liquor tax per unit)
    total           = sum of bases + sum of VAT + liquor tax

Money is rounded half up to whole pesos. A line without a known VAT counts as 0 %.
Never carries cost, margins, supports or rule data.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.modules.quotations.domain import vocabulary
from app.modules.quotations.domain.entities import QuotationDetail, QuotationLine, SellerContact

ZERO = Decimal(0)
HUNDRED = Decimal(100)
PESO = Decimal(1)
# Shown instead of the SKU of a product SIESA does not have yet.
LOCAL_REFERENCE = "Por crear"
_MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def pesos(value: Decimal) -> Decimal:
    """Rounded half up to whole pesos."""

    return value.quantize(PESO, rounding=ROUND_HALF_UP)


def offer_price(line: QuotationLine) -> Decimal | None:
    """The price the customer is offered: the new price, else the last one it paid.

    A local product has no last price: only its new price.
    """

    if line.is_local:
        return line.new_price
    return line.new_price if line.new_price is not None else line.last_price


def in_offer(line: QuotationLine, scope: str) -> bool:
    """Whether the line goes in the offer (``changed`` or ``all``); inactive lines never."""

    if not line.is_active:
        return False
    if scope == vocabulary.OFFER_ALL:
        return offer_price(line) is not None
    return line.new_price is not None


def vat_points(line: QuotationLine) -> Decimal:
    """The line's VAT in points (19, 5, 0); unknown counts as 0."""

    value = line.vat_pct if line.vat_pct is not None else ZERO
    return Decimal(int(value)) if value == value.to_integral_value() else value.normalize()


@dataclass(frozen=True)
class VatGroup:
    vat_pct: Decimal
    base: Decimal
    vat: Decimal


@dataclass(frozen=True)
class OfferSummary:
    item_count: int
    subtotal: Decimal
    vat_groups: tuple[VatGroup, ...]
    liquor_tax: Decimal
    total: Decimal

    @property
    def vat(self) -> Decimal:
        return sum((group.vat for group in self.vat_groups), ZERO)


@dataclass(frozen=True)
class LineAmounts:
    """What one quantity of a line comes to."""

    base: Decimal
    vat: Decimal
    liquor_tax: Decimal

    @property
    def total(self) -> Decimal:
        return self.base + self.vat + self.liquor_tax


def amounts(line: QuotationLine) -> LineAmounts | None:
    """Base, VAT and liquor tax of the line's quantity at its offer price."""

    price = offer_price(line)
    if line.quantity is None or price is None:
        return None
    base = pesos(line.quantity * price)
    vat = pesos(base * vat_points(line) / HUNDRED)
    liquor = pesos(line.quantity * (line.value_tax_amount or ZERO))
    return LineAmounts(base, vat, liquor)


def offer_lines(detail: QuotationDetail) -> list[QuotationLine]:
    """The lines the offer carries, in position order."""

    scope = detail.quotation.offer_scope
    return [line for line in detail.lines if in_offer(line, scope)]


def offer_summary(detail: QuotationDetail) -> OfferSummary | None:
    """Totals of the offer lines that have a quantity; ``None`` unless quantities are on."""

    if not detail.quotation.quote_quantities:
        return None
    groups: dict[Decimal, tuple[Decimal, Decimal]] = {}
    count = 0
    liquor = ZERO
    for line in offer_lines(detail):
        result = amounts(line)
        if result is None:
            continue
        count += 1
        rate = vat_points(line)
        base, vat = groups.get(rate, (ZERO, ZERO))
        groups[rate] = (base + result.base, vat + result.vat)
        liquor += result.liquor_tax
    vat_groups = tuple(
        VatGroup(rate, base, vat)
        for rate, (base, vat) in sorted(groups.items(), key=lambda item: -item[0])
    )
    subtotal = sum((group.base for group in vat_groups), ZERO)
    total = subtotal + sum((group.vat for group in vat_groups), ZERO) + liquor
    return OfferSummary(count, subtotal, vat_groups, liquor, total)


def long_date(day: date) -> str:
    """``15 de noviembre de 2026``."""

    return f"{day.day} de {_MONTHS[day.month - 1]} de {day.year}"


@dataclass(frozen=True)
class OfferRow:
    """One item of the customer offer."""

    reference: str
    description: str | None
    unit: str | None
    price: Decimal | None
    vat_pct: Decimal
    liquor_tax: Decimal | None
    price_with_vat: Decimal | None
    quantity: Decimal | None = None
    subtotal: Decimal | None = None
    vat: Decimal | None = None
    total: Decimal | None = None
    # New price against the last one (fraction), shown only with ``include_variation``.
    variation_pct: Decimal | None = None


@dataclass(frozen=True)
class OfferDocument:
    """Everything the customer workbook prints, already decided."""

    customer_name: str | None
    customer_nit: str
    channel: str | None
    issued: date
    valid_until: date | None
    seller: SellerContact | None
    rows: tuple[OfferRow, ...]
    quote_quantities: bool
    summary: OfferSummary | None
    conditions: tuple[str, ...]
    include_variation: bool = False
    # A business group's quotation goes to the group (2026-11-19): its name and how many
    # customers (NITs) it has; ``customer_name`` is the group's name then.
    business_group: str | None = None
    group_customers: int | None = None


def _row(line: QuotationLine, quantities: bool) -> OfferRow:
    price = offer_price(line)
    rate = vat_points(line)
    liquor = line.value_tax_amount
    with_vat = None
    if price is not None:
        with_vat = pesos(price * (1 + rate / HUNDRED) + (liquor or ZERO))
    row = OfferRow(
        reference=LOCAL_REFERENCE if line.is_local else line.sku,
        description=line.description,
        unit=line.unit,
        price=price,
        vat_pct=rate,
        liquor_tax=liquor,
        price_with_vat=with_vat,
        variation_pct=line.variation_pct,
    )
    if not quantities:
        return row
    result = amounts(line)
    if result is None:
        return row
    return replace(
        row,
        quantity=line.quantity,
        subtotal=result.base,
        vat=result.vat,
        total=result.total,
    )


def offer_document(detail: QuotationDetail, issued: date) -> OfferDocument:
    """The customer offer of a quotation, issued on ``issued``.

    The conditions start with the validity, then the advisor's own.
    """

    quotation = detail.quotation
    quantities = quotation.quote_quantities
    conditions: list[str] = []
    if quotation.valid_until is not None:
        conditions.append(f"Precios válidos hasta el {long_date(quotation.valid_until)}")
    conditions.extend(quotation.conditions)
    return OfferDocument(
        customer_name=quotation.customer_name,
        customer_nit=quotation.customer_nit,
        channel=quotation.channel,
        issued=issued,
        valid_until=quotation.valid_until,
        seller=quotation.seller_contact if quotation.include_seller else None,
        rows=tuple(_row(line, quantities) for line in offer_lines(detail)),
        quote_quantities=quantities,
        summary=offer_summary(detail),
        conditions=tuple(conditions),
        include_variation=quotation.include_variation,
        business_group=quotation.business_group,
        group_customers=quotation.group_customers,
    )


def clean_conditions(values: Sequence[str]) -> tuple[str, ...]:
    """Trimmed conditions without the empty ones, in order (the caller checks the limits)."""

    return tuple(text for text in (value.strip() for value in values) if text)


__all__ = [
    "LOCAL_REFERENCE",
    "LineAmounts",
    "OfferDocument",
    "OfferRow",
    "OfferSummary",
    "VatGroup",
    "amounts",
    "clean_conditions",
    "in_offer",
    "long_date",
    "offer_document",
    "offer_lines",
    "offer_price",
    "offer_summary",
    "pesos",
    "vat_points",
]
