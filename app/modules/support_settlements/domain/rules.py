"""How a month of sales is settled against the supplier supports (2026-11-24).

Rules agreed with BI, checked against its September 2026 workbooks (Familia Sancela and Team
Foods):

- **Which supports**: táctico cliente and actividad temporal, funded by the supplier (not
  ``jdh``), not voided, switched on, in force on the sale's day. A margin exception (JDH's own
  profitability) is never claimed.
- **Scope**: the support's customer, group, branch, regional, channel, business unit,
  warehouse and promotional warehouses are checked with the sale's own branch
  (:func:`~app.modules.order_validation.domain.rules.scope_of`), not the union of the
  customer's branches.
- **Cost** (two bases, 2026-11-24; every month is settled with both):
  - ``cotizacion``: the supplier's SIESA quotation in force on the sale's day; without one,
    the invoice's cost, and the line is marked.
  - ``inventario``: the inventory cost billed on the line (``consumo.ventas.costo`` ÷ quantity).
- **List price** (:func:`list_price`): the chosen cost at the product's **Food Service**
  margin (``margin_list`` of the margin master), ``cost / (1 − margin)``, whatever the
  customer's channel (2026-11-24).
- **Amount per unit** (:func:`unit_amount`), all with the chosen cost: % on cost, % on list
  price, $ per unit or box (or kilo), $ supported cost (``cost − supported``, never below zero)
  and % guaranteed margin (``cost − supported cost``, the supported cost being the price sold
  at the guaranteed margin).
- **Discount passed to the customer** (% types only): when the customer was billed above the
  negotiated price, the supplier's % drops by the discount points not given:
  ``% real = % − max(0, negotiated discount − granted discount)``, both over the list price.
- **Several supports**: copies of the same support (same class, type, value, unit and
  reference: a business group's copies, a group and a NIT copy) count once; really different
  supports add up and the line is marked. A supplier-margin request's copy replaces its base.
- **Returns** subtract: their quantity is negative. Lines with no sale value (gifts, internal
  consumption, services) are not settled.
- **Minimum quantity**: the month's quantity of the customer and reference must reach it.
- Supports in review, with tiers, or already discounted on the supplier's invoice
  (``pie_factura``, ``costo_neto_facturado``) are listed but not claimed.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal

from app.modules.commercial_masters.domain.margins import margin_for
from app.modules.order_validation.domain.entities import Agreement, CustomerScope, OrderLine
from app.modules.order_validation.domain.rules import APPLIES, scope_of
from app.modules.supplier_supports.domain import pricing
from app.modules.support_settlements.domain.entities import (
    ALREADY_DISCOUNTED,
    COST_BASES,
    COST_INVENTORY,
    COST_QUOTE,
    IN_REVIEW,
    INVOICE,
    INVOICE_COST,
    MINIMUM_NOT_MET,
    NO_VALUE,
    PARTIAL_DISCOUNT,
    QUOTE,
    SETTLED,
    SEVERAL_SUPPORTS,
    TIERS,
    CustomerBranch,
    Product,
    Quote,
    ReferenceTotals,
    SaleLine,
    SettledItem,
    SettledSale,
    SettlementResult,
    SettlementSupport,
)

ZERO = Decimal(0)
ONE = Decimal(1)
CENT = Decimal("0.01")
UNIT_QUANTUM = Decimal("0.0001")
# Percentages are kept at six decimals, the precision of the support's columns.
PCT_QUANTUM = Decimal("0.000001")
# Discount points below this are rounding, not a discount withheld.
DISCOUNT_TOLERANCE = Decimal("0.0001")
SETTLED_KINDS = ("tactico_cliente", "actividad_temporal")
DISCOUNTED_ON_INVOICE = ("pie_factura", "costo_neto_facturado")
PERCENT_METHODS = ("pct_costo", "pct_precio_lista")
KILO = "kg"
# The margin the list price of a settlement is taken at: «Margen Food Service».
FOOD_SERVICE = "margin_list"


def _label(value: str | None) -> str:
    return " ".join((value or "").upper().split())


def quote_on(quotes: Sequence[Quote], day, co: str | None, unit: str = "UNID") -> Quote | None:
    """The supplier quotation in force on ``day``: the latest activation on or before it, of
    the line's operation center first, then one with no center, then the highest cost."""

    def usable(strict: bool, wanted: str) -> list[Quote]:
        return [
            quote
            for quote in quotes
            if quote.unit == wanted
            and quote.since <= day
            and (not strict or quote.until is None or quote.until >= day)
        ]

    for wanted in dict.fromkeys((unit, "UNID")):
        candidates = usable(True, wanted) or usable(False, wanted)
        if not candidates:
            continue
        latest: dict[str | None, Quote] = {}
        for quote in sorted(candidates, key=lambda item: item.since):
            latest[(quote.co or "").strip() or None] = quote
        if co and co.strip() in latest:
            return latest[co.strip()]
        if None in latest:
            return latest[None]
        return max(latest.values(), key=lambda item: item.cost)
    return None


def _scope(line: SaleLine, customer: CustomerBranch | None) -> CustomerScope | None:
    """The sale's own branch as the scope of the supports."""

    if customer is None:
        return None
    return CustomerScope(
        nit=customer.nit,
        channels=frozenset({customer.channel} - {None}),
        business_units=frozenset({customer.business_unit} - {None}),
        regionals=frozenset({customer.billing_center} - {None}),
        branches=frozenset({customer.branch_id} - {None}),
        business_group=customer.business_group,
    )


def applies(
    support: SettlementSupport,
    line: SaleLine,
    customer: CustomerBranch | None,
    supplier_code: str | None,
) -> bool:
    """Whether the support covers the sale line on its day."""

    agreement = Agreement(
        id=support.id,
        # A support of one reference is that reference's, whatever supplier the master says.
        supplier_code=None if support.product_sku else support.supplier_code,
        kind=support.kind,
        calc_method=support.calc_method,
        status=support.status,
        customer_nit=None if support.group_wide else support.customer_nit,
        product_sku=support.product_sku,
        regionals=support.regionals,
        business_units=support.business_units,
        channels=support.channels,
        warehouse=support.warehouse,
        valid_from=support.valid_from,
        valid_to=support.valid_to,
        customer_branch_id=support.customer_branch_id,
        customer_group=support.customer_group,
        promo_warehouses=support.promo_warehouses,
    )
    scope = _scope(line, customer)
    probe = OrderLine(
        row_number=0,
        document=line.document or "",
        customer_nit=line.nit,
        sku=line.sku,
        supplier_code=supplier_code,
        channel=customer.channel if customer else None,
        warehouse=line.warehouse_code,
    )
    if scope_of(agreement, probe, scope, line.sale_date) == APPLIES:
        return True
    # A support written with the warehouse's name instead of its code.
    if support.warehouse and line.warehouse_name:
        by_name = replace(probe, warehouse=line.warehouse_name)
        return scope_of(agreement, by_name, scope, line.sale_date) == APPLIES
    return False


def _family(support: SettlementSupport) -> tuple:
    """What makes two stored supports the same support (copies count once)."""

    return (
        support.kind,
        support.calc_method,
        support.supplier_value,
        support.value_unit,
        support.target_margin_pct,
        support.product_sku,
    )


def distinct_supports(
    supports: Iterable[SettlementSupport], nit: str | None
) -> list[SettlementSupport]:
    """One support per family, and a request's copy instead of its base.

    The copy kept is the customer's own one, then a group copy of the customer, then the
    lowest id, so a recalculation always names the same support.
    """

    matched = list(supports)
    replaced = {item.replaces_support_id for item in matched if item.replaces_support_id}
    kept: dict[tuple, SettlementSupport] = {}

    def rank(item: SettlementSupport) -> tuple:
        own = item.customer_nit is not None and item.customer_nit == nit
        return (not (own and not item.group_wide), not own, str(item.id))

    for item in sorted((item for item in matched if item.id not in replaced), key=rank):
        kept.setdefault(_family(item), item)
    return list(kept.values())


def list_price(base_cost: Decimal | None, product: Product | None) -> Decimal | None:
    """``cost / (1 − margin)`` with the product's Food Service margin (``margin_list``)."""

    if base_cost is None or product is None:
        return None
    return pricing.price_at_margin(base_cost, margin_for(product.margins, FOOD_SERVICE))


def _per_unit_money(
    support: SettlementSupport, line: SaleLine, product: Product | None
) -> Decimal | None:
    """A money value of the support per inventory unit: per box ÷ the pack factor, per kilo ×
    the kilos of each unit sold."""

    value = support.supplier_value
    if value is None:
        return None
    if support.value_unit == KILO:
        if not line.kg or not line.quantity:
            return None
        return value * abs(line.kg / line.quantity)
    pack = support.pack_factor or (product.pack_factor if product else None)
    return pricing.per_unit(value, support.value_unit, pack)


def settled_percent(
    support: SettlementSupport,
    base_cost: Decimal,
    price_list: Decimal | None,
    unit_price: Decimal | None,
) -> tuple[Decimal, Decimal]:
    """The % the supplier pays after the discount the customer did not get, and the points
    taken off (``0`` when the customer got the whole discount, or nothing tells)."""

    value = support.supplier_value or ZERO
    if not price_list or price_list <= 0 or not unit_price or unit_price <= 0:
        return value, ZERO
    base = base_cost if support.calc_method == "pct_costo" else price_list
    amount = base * value
    authorized = support.supported_margin_pct
    if authorized is not None and authorized < ONE:
        negotiated_price = (base_cost - amount) / (ONE - authorized)
    else:
        negotiated_price = price_list - amount
    negotiated = ONE - negotiated_price / price_list
    granted = ONE - unit_price / price_list
    gap = max(ZERO, negotiated - granted)
    if gap < DISCOUNT_TOLERANCE:
        return value, ZERO
    return max(ZERO, value - gap).quantize(PCT_QUANTUM, ROUND_HALF_UP), gap


def unit_amount(
    support: SettlementSupport,
    line: SaleLine,
    product: Product | None,
    base_cost: Decimal | None,
) -> tuple[Decimal | None, Decimal | None, Decimal | None, str]:
    """The supplier's money per inventory unit of the line, with the chosen cost.

    Returns:
        tuple: ``(amount or None, negotiated %, settled %, how)``; the two percentages only
        for the % types.
    """

    method = support.calc_method
    unit_price = abs(line.net / line.quantity) if line.quantity else None
    if method in PERCENT_METHODS:
        if base_cost is None:
            return None, support.supplier_value, None, "sin costo"
        price_list = list_price(base_cost, product)
        settled, gap = settled_percent(support, base_cost, price_list, unit_price)
        if method == "pct_costo":
            how = f"{settled:.4%} sobre el costo {base_cost:,.2f}"
            amount = base_cost * settled
        else:
            if price_list is None:
                return None, support.supplier_value, settled, "sin margen para el precio lista"
            how = f"{settled:.4%} sobre el precio lista {price_list:,.2f}"
            amount = price_list * settled
        if gap > 0:
            how += f" (negociado {support.supplier_value:.4%}, menos {gap:.4%} no otorgado)"
        return amount, support.supplier_value, settled, how
    if method in ("valor_unidad", "valor_precio_lista"):
        money = _per_unit_money(support, line, product)
        if money is None:
            return (
                None,
                None,
                None,
                "sin kilos en la venta" if support.value_unit == KILO else "sin valor",
            )
        return money, None, None, f"{money:,.2f} por unidad"
    if method == "costo_neto":
        supported = _per_unit_money(support, line, product)
        if supported is None or base_cost is None:
            return None, None, None, "sin costo apoyado o sin costo"
        return (
            max(ZERO, base_cost - supported),
            None,
            None,
            f"costo {base_cost:,.2f} − costo apoyado {supported:,.2f}",
        )
    if method == pricing.GUARANTEED_METHOD:
        amount = pricing.unit_amount(
            method,
            None,
            cost=base_cost,
            list_price=list_price(base_cost, product),
            guaranteed_margin=support.target_margin_pct,
            sale_price=unit_price,
            supplier_min_margin=support.supplier_min_margin_pct,
        )
        if amount is None or base_cost is None:
            return None, None, None, "sin costo o margen garantizado"
        amount = max(ZERO, amount)
        return (
            amount,
            None,
            None,
            f"margen garantizado {support.target_margin_pct:.2%}: costo {base_cost:,.2f}"
            f" − costo apoyado {base_cost - amount:,.2f}",
        )
    return None, None, None, "tipo de apoyo sin dinero"


def invoice_cost(line: SaleLine) -> Decimal | None:
    """The inventory cost billed on the line, per unit (``costo`` ÷ quantity)."""

    return abs(line.cost / line.quantity) if line.cost and line.quantity else None


def _base_cost(
    line: SaleLine,
    product: Product | None,
    quotes: Mapping[str, Sequence[Quote]],
    cost_basis: str,
) -> tuple[Decimal | None, str]:
    """The unit cost the line is settled with, and where it came from."""

    if cost_basis == COST_INVENTORY:
        return invoice_cost(line), INVOICE
    unit = "KL" if product and _label(product.inventory_unit) == "KL" else "UNID"
    quote = quote_on(quotes.get(line.sku, ()), line.sale_date, line.co, unit)
    if quote is not None:
        return quote.cost, QUOTE
    return invoice_cost(line), INVOICE


def _status(support: SettlementSupport, amount: Decimal | None) -> str:
    if support.status == "pendiente_revision":
        return IN_REVIEW
    if support.has_tiers:
        return TIERS
    if support.settlement_method in DISCOUNTED_ON_INVOICE:
        return ALREADY_DISCOUNTED
    if amount is None:
        return NO_VALUE
    return SETTLED


def build_settlement(
    *,
    sales: Sequence[SaleLine],
    supports: Sequence[SettlementSupport],
    products: Mapping[str, Product],
    customers: Mapping[str, CustomerBranch],
    quotes: Mapping[str, Sequence[Quote]],
    cost_basis: str = COST_QUOTE,
) -> SettlementResult:
    """Settle the month's sales lines against the supports.

    Args:
        sales: Every sale and return line of the candidate references in the month.
        supports: The supports that may apply (any day of the month).
        products: Products by SKU (supplier, pack factor, margins).
        customers: Customer branches by ``cliente_key``.
        quotes: Supplier quotations by SKU.
        cost_basis: ``cotizacion`` (the supplier's quotation) or ``inventario`` (the
            inventory cost billed on the line).

    Returns:
        SettlementResult: The lines with supports, and the month's totals per reference.
    """

    if cost_basis not in COST_BASES:
        raise ValueError(f"Unknown cost basis: {cost_basis}")
    by_sku: dict[str, list[SettlementSupport]] = defaultdict(list)
    for support in supports:
        if support.kind in SETTLED_KINDS and support.funding != "jdh":
            by_sku[support.product_sku or ""].append(support)

    settled: list[SettledSale] = []
    for line in sales:
        product = products.get(line.sku)
        supplier_code = product.supplier_code if product else None
        customer = customers.get(line.customer_key or "")
        if not line.quantity or not line.net:
            continue  # gifts, internal consumption, services: no sale to support
        candidates = [
            support
            for support in (*by_sku.get(line.sku, ()), *by_sku.get("", ()))
            if (support.product_sku or support.supplier_code == supplier_code)
            and applies(support, line, customer, supplier_code)
        ]
        if not candidates:
            continue
        chosen = distinct_supports(candidates, line.nit)
        flags: list[str] = []
        base_cost, source = _base_cost(line, product, quotes, cost_basis)
        if cost_basis == COST_QUOTE and source == INVOICE:
            flags.append(INVOICE_COST)
        items = []
        for support in chosen:
            per_unit, negotiated, settled_pct, how = unit_amount(support, line, product, base_cost)
            if negotiated is not None and settled_pct is not None and settled_pct < negotiated:
                flags.append(PARTIAL_DISCOUNT)
            items.append(
                SettledItem(
                    support_id=support.id,
                    support_version=support.version,
                    kind=support.kind,
                    calc_method=support.calc_method,
                    status=_status(support, per_unit),
                    supplier_value=support.supplier_value,
                    value_unit=support.value_unit,
                    valid_from=support.valid_from,
                    valid_to=support.valid_to,
                    unit_amount=(per_unit or ZERO).quantize(UNIT_QUANTUM, ROUND_HALF_UP),
                    amount=((per_unit or ZERO) * line.quantity).quantize(CENT, ROUND_HALF_UP),
                    negotiated_pct=negotiated,
                    settled_pct=settled_pct,
                    detail=how,
                )
            )
        if len(items) > 1:
            flags.append(SEVERAL_SUPPORTS)
        settled.append(
            SettledSale(
                line=line,
                supplier_code=supplier_code or (chosen[0].supplier_code if chosen else None),
                supplier_name=chosen[0].supplier_name if chosen else None,
                description=product.description if product else None,
                customer=customer,
                base_unit_cost=base_cost,
                cost_source=source,
                items=tuple(items),
                flags=tuple(dict.fromkeys(flags)),
            )
        )

    settled = _apply_minimums(settled, {support.id: support for support in supports})
    return SettlementResult(
        sales=tuple(settled),
        references=tuple(_references(sales, settled, products, supports)),
        sales_read=len(sales),
    )


def _apply_minimums(sales: list[SettledSale], supports: Mapping) -> list[SettledSale]:
    """A support with a minimum quantity pays only when the customer's month of that
    reference reaches it (returns included)."""

    totals: dict[tuple, Decimal] = defaultdict(Decimal)
    for sale in sales:
        for item in sale.items:
            minimum = supports[item.support_id].min_quantity
            if minimum:
                totals[(item.support_id, sale.line.nit, sale.line.sku)] += sale.line.quantity
    if not totals:
        return sales
    result = []
    for sale in sales:
        items = []
        for item in sale.items:
            minimum = supports[item.support_id].min_quantity
            key = (item.support_id, sale.line.nit, sale.line.sku)
            if minimum and item.status == SETTLED and totals[key] < minimum:
                item = replace(item, status=MINIMUM_NOT_MET)
            items.append(item)
        result.append(replace(sale, items=tuple(items)))
    return result


def _references(
    sales: Sequence[SaleLine],
    settled: Sequence[SettledSale],
    products: Mapping[str, Product],
    supports: Sequence[SettlementSupport],
) -> list[ReferenceTotals]:
    """The month of every reference with at least one supported line: all its sales (to
    recompose its margin) and what its supports pay."""

    names = {support.supplier_code: support.supplier_name for support in supports}
    by_sku: dict[str, list[SettledSale]] = defaultdict(list)
    for sale in settled:
        by_sku[sale.line.sku].append(sale)
    sums: dict[str, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO, ZERO])
    for line in sales:
        if line.sku in by_sku:
            totals = sums[line.sku]
            totals[0] += line.quantity
            totals[1] += line.net
            totals[2] += line.cost
    result = []
    for sku, lines in sorted(by_sku.items()):
        product = products.get(sku)
        quantity, net, cost = sums[sku]
        code = lines[0].supplier_code
        result.append(
            ReferenceTotals(
                supplier_code=code,
                supplier_name=names.get(code) or lines[0].supplier_name,
                sku=sku,
                description=product.description if product else None,
                quantity=quantity,
                net=net,
                cost=cost,
                amount=sum((sale.amount for sale in lines), ZERO),
                flagged_amount=sum(
                    (
                        item.amount
                        for sale in lines
                        for item in sale.items
                        if item.status != SETTLED
                    ),
                    ZERO,
                ),
                supported_lines=len(lines),
            )
        )
    return result


def recomposed_margin(net: Decimal, cost: Decimal, amount: Decimal) -> Decimal | None:
    """``(sale − cost + support) / sale``: the margin with the supplier's money back."""

    if not net:
        return None
    return (net - cost + amount) / net


def margin(net: Decimal, cost: Decimal) -> Decimal | None:
    """``(sale − cost) / sale``."""

    if not net:
        return None
    return (net - cost) / net


__all__ = [
    "applies",
    "build_settlement",
    "distinct_supports",
    "invoice_cost",
    "list_price",
    "margin",
    "quote_on",
    "recomposed_margin",
    "settled_percent",
    "unit_amount",
]
