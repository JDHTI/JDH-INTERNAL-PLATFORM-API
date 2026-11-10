"""How a quotation line is priced: margins, the support it carries and the general rule.

::

    support per unit    = share of the support on cost x cost
    margin              = (price - cost) / price
    margin with support = (price - (cost - support)) / price
    minimum margin      = target margin - allowed points
    minimum price       = (cost - support) / (1 - minimum margin)

Supports are matched exactly as the retained orders validator does
(:func:`app.modules.order_validation.domain.rules.scope_of`), with the customer and the SKU and
no warehouse or quantity: a quotation has neither.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import date
from decimal import ROUND_HALF_UP, ROUND_UP, Decimal
from uuid import UUID

from app.modules.commercial_masters.domain.margins import channel_field
from app.modules.order_validation.domain.entities import Agreement, CustomerScope, OrderLine
from app.modules.order_validation.domain.rules import (
    AMOUNT_METHODS,
    APPLIES,
    BASIS,
    pct,
    required_margin,
    scope_of,
    share_of_cost,
)
from app.modules.quotations.domain import pricing, vocabulary
from app.modules.quotations.domain.entities import (
    AppliedSupport,
    Branch,
    CostInfo,
    CustomerContext,
    ItemInfo,
    LastSale,
    ListPrice,
    ProductMaster,
    QuotationLine,
    QuotationPolicy,
    SupportCandidate,
    ValueTaxKey,
)

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)
MONEY = Decimal("0.0001")
PESO = Decimal(1)
RATIO = Decimal("0.000001")
_TARGET_METHODS = ("margen_minimo", "puntos_margen")
_LOCAL_SKU = re.compile(rf"^{re.escape(vocabulary.LOCAL_SKU_PREFIX)}([0-9]+)$", re.IGNORECASE)


def money(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(MONEY, rounding=ROUND_HALF_UP)


def ratio(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(RATIO, rounding=ROUND_HALF_UP)


def margin(price: Decimal | None, cost: Decimal | None, support: Decimal = ZERO) -> Decimal | None:
    """``(price - (cost - support)) / price``; ``None`` without a positive price or a cost."""

    if price is None or cost is None or price <= 0:
        return None
    return ratio((price - (cost - support)) / price)


def variation(new_price: Decimal | None, last_price: Decimal | None) -> Decimal | None:
    """``new / last - 1``."""

    if new_price is None or last_price is None or last_price <= 0:
        return None
    return ratio(new_price / last_price - ONE)


def minimum_margin(target: Decimal | None, policy: QuotationPolicy) -> Decimal | None:
    return None if target is None else ratio(target - policy.max_points_below_target)


def minimum_price(
    cost: Decimal | None, support: Decimal, floor_margin: Decimal | None
) -> Decimal | None:
    """Lowest price in whole pesos whose margin with support reaches ``floor_margin``."""

    if cost is None or floor_margin is None or floor_margin >= ONE:
        return None
    price = (cost - support) / (ONE - floor_margin)
    if price <= 0:
        return ZERO
    return price.quantize(PESO, rounding=ROUND_UP)


def price_at_margin(
    cost: Decimal | None, support: Decimal, target: Decimal | None, family: str
) -> Decimal | None:
    """``(cost - support) / (1 - target)`` in whole pesos, rounded as the list family does."""

    if cost is None or target is None or target >= ONE:
        return None
    price = (cost - support) / (ONE - target)
    if price <= 0:
        return None
    return pricing.round_price(price, family)


def price_for_margin(line: QuotationLine, target: Decimal, family: str) -> Decimal | None:
    """The price that gives ``line`` the margin ``target`` (with its support).

    Never under the line's minimum when the margin asked for meets the rule: rounding to the
    peso must not turn a valid margin into one below the minimum.
    """

    price = price_at_margin(line.cost, line.support_amount, target, family)
    if price is None:
        return None
    floor = line.min_margin_pct
    if floor is not None and target >= floor and line.min_price is not None:
        price = max(price, line.min_price)
    return price


def compliance(line: QuotationLine) -> str | None:
    """How a new price stands against the rule; ``None`` without a new price.

    A local product (not in SIESA) has no cost and never reaches the files: the rule does
    not judge it, so it is ``None`` too.
    """

    if line.new_price is None or line.is_local:
        return None
    if line.cost is None:
        return vocabulary.NO_COST
    if line.min_margin_pct is None:
        return vocabulary.NO_TARGET
    reached = line.new_margin_with_support_pct
    if reached is not None and reached.quantize(RATIO) >= line.min_margin_pct.quantize(RATIO):
        return vocabulary.MEETS
    return vocabulary.BELOW_MINIMUM


# ------------------------------------------------------------------ customer


def _majority(values: Iterable[str | None]) -> tuple[str | None, bool]:
    """Most common non-empty value and whether there was more than one."""

    counts = Counter(value for value in values if value)
    if not counts:
        return None, False
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0], len(counts) > 1


def billing_center(branches: Sequence[Branch]) -> tuple[str | None, bool]:
    """The center a quotation starts from, and whether the branches bill from several.

    If any branch bills from a Coast center, the most frequent Coast center among them wins
    (the higher liquor tax); otherwise the most frequent center.
    """

    centers = [item.billing_center_code for item in branches]
    coast = [code for code in centers if code in pricing.COAST_CENTERS]
    center, _ = _majority(coast) if coast else _majority(centers)
    _, differ = _majority(centers)
    return center, differ


def customer_context(
    nit: str, branches: Sequence[Branch], price_list_customers: int | None = None
) -> CustomerContext:
    """Merge the branches of a customer: the majority among the active ones decides, but a
    Coast billing center among them wins (see :func:`billing_center`).

    Args:
        nit (str): The customer's NIT.
        branches (Sequence[Branch]): Every branch the warehouse has for the NIT.
        price_list_customers (int | None): Active customers sharing the chosen price list.

    Returns:
        CustomerContext: Channel, price list, operation center and seller, with warnings.
    """

    active = [item for item in branches if item.is_active] or list(branches)
    warnings: list[str] = []
    if not any(item.is_active for item in branches):
        warnings.append("cliente_inactivo")
    channel_code, _ = _majority(item.channel_code for item in active)
    channel = next((item.channel for item in active if item.channel_code == channel_code), None)
    if channel_code is None:
        warnings.append("sin_canal")
    price_list_id, lists_differ = _majority(item.price_list_id for item in active)
    if lists_differ:
        warnings.append("listas_distintas")
    if price_list_id is None:
        warnings.append("sin_lista")
        price_list_id = vocabulary.DEFAULT_PRICE_LIST
    list_name = next(
        (item.price_list_name for item in active if item.price_list_id == price_list_id), None
    )
    center, centers_differ = billing_center(active)
    if centers_differ:
        warnings.append("co_distintos")
    seller, _ = _majority(item.salesperson_code for item in active)
    seller_name = next(
        (item.salesperson_name for item in active if item.salesperson_code == seller), None
    )
    if price_list_customers is not None and price_list_customers > 1:
        warnings.append("lista_compartida")
    name = next((item.legal_name for item in active if item.legal_name), None)
    return CustomerContext(
        nit=nit,
        name=name,
        channel_code=channel_code,
        channel=channel,
        price_list_id=price_list_id,
        price_list_name=list_name,
        price_list_customers=price_list_customers,
        billing_center_code=center,
        salesperson_code=seller,
        salesperson_name=seller_name,
        warnings=tuple(warnings),
    )


# ------------------------------------------------------------------ supports


def _candidate(agreement: Agreement, reason: str) -> SupportCandidate:
    return SupportCandidate(agreement.id, agreement.calc_method, agreement.program, reason)


def _applied(
    agreement: Agreement,
    amount: Decimal | None,
    share: Decimal | None,
    required: Decimal | None,
    detail: str,
) -> AppliedSupport:
    conditions = None
    if agreement.min_quantity is not None:
        conditions = f"Cantidad mínima {agreement.min_quantity.normalize():f}"
    return AppliedSupport(
        support_id=agreement.id,
        kind=agreement.kind,
        calc_method=agreement.calc_method,
        program=agreement.program,
        amount=money(amount),
        share_of_cost=ratio(share),
        required_margin_pct=ratio(required),
        detail=detail,
        conditions=conditions,
        needs_review=agreement.status == "pendiente_revision",
        valid_from=agreement.valid_from,
        valid_to=agreement.valid_to,
    )


def _target_floor(agreement: Agreement, product_target: Decimal | None) -> Decimal | None:
    """Minimum margin an exception support authorizes (no money of the supplier)."""

    if agreement.calc_method == "margen_minimo":
        return agreement.min_margin_pct
    base = agreement.list_margin_pct if agreement.list_margin_pct is not None else product_target
    if base is None or agreement.supplier_value is None:
        return None
    return base - agreement.supplier_value


def pick_support(
    agreements: Sequence[Agreement],
    *,
    nit: str,
    sku: str,
    supplier_code: str | None,
    channel: str | None,
    scope: CustomerScope | None,
    cost: Decimal | None,
    product_target: Decimal | None,
    day: date,
) -> tuple[AppliedSupport | None, tuple[SupportCandidate, ...], tuple[str, ...]]:
    """The support a quotation line is priced with.

    Among the supports in force today that cover the customer and the SKU, the one that
    brings the most money per unit of cost wins. A margin exception (``margen_minimo``,
    ``puntos_margen``) brings no money: it only applies when no support with money does,
    and then its authorized minimum is the target. Supports in review or for one
    warehouse are listed as candidates and never applied.

    Returns:
        tuple: The applied support (or ``None``), the candidates not applied and the line
        problems it raises.
    """

    probe = OrderLine(
        row_number=0,
        document="",
        customer_nit=nit,
        sku=sku,
        supplier_code=supplier_code,
        channel=channel,
    )
    money_supports: list[tuple[Decimal, Agreement, str]] = []
    exceptions: list[tuple[Decimal, Agreement]] = []
    candidates: list[SupportCandidate] = []
    problems: list[str] = []
    for agreement in agreements:
        if agreement.warehouse:
            if scope_of(replace(agreement, warehouse=None), probe, scope, day) == APPLIES:
                candidates.append(
                    _candidate(agreement, f"Solo para la bodega {agreement.warehouse}")
                )
                problems.append("apoyo_fuera_bodega")
            continue
        if scope_of(agreement, probe, scope, day) != APPLIES:
            continue
        if agreement.status == "pendiente_revision":
            candidates.append(_candidate(agreement, "Apoyo en revisión"))
            problems.append("apoyo_en_revision")
            continue
        method = agreement.calc_method
        if method in AMOUNT_METHODS:
            share, how = share_of_cost(agreement)
            if share is None:
                candidates.append(_candidate(agreement, f"No se puede calcular: {how}"))
            else:
                money_supports.append((share, agreement, how))
        elif method in _TARGET_METHODS:
            floor = _target_floor(agreement, product_target)
            if floor is None:
                candidates.append(_candidate(agreement, "Sin margen mínimo autorizado"))
            else:
                exceptions.append((floor, agreement))
        else:
            candidates.append(_candidate(agreement, "Rebate sobre ventas: se liquida por periodo"))

    if money_supports:
        share, chosen, how = max(
            money_supports, key=lambda item: (item[0], item[1].valid_to or date.max)
        )
        for _, other, _ in money_supports:
            if other.id != chosen.id:
                candidates.append(_candidate(other, "Otro apoyo trae más dinero"))
        amount = None if cost is None else share * cost
        required = required_margin(chosen, product_target)
        applied = _applied(chosen, amount, share, required, f"Apoyo de {how}")
        if chosen.min_quantity is not None:
            problems.append("apoyo_condicionado")
        for _, other in exceptions:
            candidates.append(_candidate(other, "Hay un apoyo con dinero"))
        return applied, tuple(candidates), tuple(dict.fromkeys(problems))
    if exceptions:
        floor, chosen = min(exceptions, key=lambda item: item[0])
        applied = _applied(chosen, ZERO, ZERO, floor, f"Margen mínimo autorizado {pct(floor)}")
        return applied, tuple(candidates), tuple(dict.fromkeys(problems))
    return None, tuple(candidates), tuple(dict.fromkeys(problems))


# ------------------------------------------------------------------ lines


def _first(*values):
    return next((value for value in values if value is not None), None)


def build_line(
    position: int,
    sku: str,
    sale: LastSale | None,
    *,
    item: ItemInfo | None = None,
    nit: str,
    channel: str | None,
    scope: CustomerScope | None,
    product: ProductMaster | None,
    supplier_name: str | None,
    cost: CostInfo | None,
    list_price: ListPrice | None,
    tax: ValueTaxKey | None,
    agreements: Sequence[Agreement],
    policy: QuotationPolicy,
    day: date,
    line_id: UUID,
    price_family: str = pricing.GENERAL,
    regional: str = vocabulary.NATIONAL,
) -> QuotationLine:
    """Price one SKU from its last sale (if any), current cost, target margin and supports.

    A SKU the advisor adds has no last sale: it comes with the warehouse item (``item``) and
    is priced from scratch. Only the liquor tax is carried as a tax (VAT is not shown).
    The cost is the highest of the supplier's quotation and the inventory of the list's
    warehouse, times the list family's factor (see :mod:`pricing`).
    """

    problems: list[str] = []
    if product is None:
        problems.append("producto_no_sincronizado")
    source = sale if sale is not None else item
    is_active = all(value.is_active for value in (source, cost) if value is not None)
    if not is_active:
        problems.append(vocabulary.INACTIVE_ITEM)
    supplier_code = _first(
        product.supplier_code if product else None, source.supplier_code if source else None
    )
    field_name = channel_field(channel)
    product_target = product.margins.get(field_name) if product else None
    basis = BASIS.get(field_name, "lista")
    cost_value, cost_source = pricing.effective_cost(
        cost.quote_cost if cost else None,
        cost.inventory_cost if cost else None,
        pricing.COST_FACTOR.get(price_family, ONE),
    )
    if cost_value is None:
        problems.append("sin_costo")
    if cost is not None and cost.discarded_quote is not None:
        problems.append("cotizacion_descartada")

    support, candidates, support_problems = pick_support(
        agreements,
        nit=nit,
        sku=sku,
        supplier_code=supplier_code,
        channel=channel,
        scope=scope,
        cost=cost_value,
        product_target=product_target,
        day=day,
    )
    problems.extend(support_problems)
    target = product_target
    if support is not None and support.required_margin_pct is not None:
        target, basis = support.required_margin_pct, "apoyo"
    if target is None:
        problems.append("sin_margen_objetivo")
    amount = support.amount if support and support.amount is not None else ZERO
    floor = minimum_margin(target, policy)
    # The liquor tax SIESA has in the customer's list; else the margin master's value for
    # the customer's regional (the Coast pays more on some SKUs).
    tax_amount = None
    if tax is not None:
        master_tax = None
        if product is not None:
            master_tax = (
                _first(product.liquor_tax_coast, product.liquor_tax)
                if regional == vocabulary.COAST
                else product.liquor_tax
            )
        tax_amount = _first(list_price.value_tax_amount if list_price else None, master_tax)
    last_price = money(sale.net_price) if sale else None
    current_with_support = margin(last_price, cost_value, amount)
    min_price = minimum_price(cost_value, amount, floor)
    optimal = price_at_margin(cost_value, amount, target, price_family)
    if optimal is not None and min_price is not None:
        optimal = max(optimal, min_price)
    return QuotationLine(
        id=line_id,
        position=position,
        sku=sku,
        description=_first(
            product.description if product else None, source.description if source else None
        ),
        # The SIESA unit id (``UNID``) the import file needs; the master copy keeps its name.
        unit=_first(source.unit if source else None, product.unit if product else None),
        pack_factor=_first(
            product.pack_factor if product else None, source.pack_factor if source else None
        ),
        supplier_code=supplier_code,
        supplier_name=supplier_name,
        last_price=last_price,
        last_gross_price=money(sale.gross_price) if sale else None,
        last_discount_pct=sale.discount_pct if sale else None,
        last_sale_date=sale.sale_date if sale else None,
        last_document=sale.document if sale else None,
        last_sale_unit=sale.sale_unit if sale else None,
        last_quantity=sale.quantity if sale else None,
        sales_in_window=sale.sales_in_window if sale else 0,
        list_price=money(list_price.price) if list_price else None,
        vat_pct=_first(product.vat_pct if product else None, source.vat_pct if source else None),
        value_tax_key=tax.key if tax and tax_amount else None,
        value_tax_name=tax.name if tax and tax_amount else None,
        value_tax_amount=money(tax_amount) if tax_amount else None,
        cost=money(cost_value),
        target_margin_pct=ratio(target),
        margin_basis=basis if target is not None else None,
        min_margin_pct=floor,
        current_margin_pct=margin(last_price, cost_value),
        current_margin_with_support_pct=current_with_support,
        min_price=min_price,
        support=support,
        support_candidates=candidates,
        problems=tuple(dict.fromkeys(problems)),
        origin=vocabulary.HISTORY if sale is not None else vocabulary.ADDED,
        quote_cost=money(cost.quote_cost) if cost and cost.quote_cost is not None else None,
        discarded_quote_cost=money(cost.discarded_quote) if cost else None,
        inventory_cost=money(cost.inventory_cost) if cost else None,
        cost_source=cost_source,
        optimal_price=optimal,
        # The support is already in the margin: a line it covers is not flagged.
        below_target=(
            is_active
            and current_with_support is not None
            and floor is not None
            and current_with_support < floor
        ),
        is_active=is_active,
        category=source.category if source else None,
    )


def local_line(
    position: int,
    sku: str,
    *,
    description: str,
    supplier_name: str | None,
    vat_pct: Decimal,
    new_price: Decimal,
    line_id: UUID,
) -> QuotationLine:
    """A product the advisor quotes that SIESA does not have (``origin: local``).

    It has no last sale, cost, target, support, unit or category: only what the advisor
    wrote and its price before VAT. The rule does not judge it and it is never exported.
    """

    line = QuotationLine(
        id=line_id,
        position=position,
        sku=sku,
        description=description,
        unit=None,
        pack_factor=None,
        supplier_code=None,
        supplier_name=supplier_name,
        last_price=None,
        last_gross_price=None,
        last_discount_pct=None,
        last_sale_date=None,
        last_document=None,
        last_sale_unit=None,
        last_quantity=None,
        sales_in_window=0,
        list_price=None,
        vat_pct=vat_pct,
        value_tax_key=None,
        value_tax_name=None,
        value_tax_amount=None,
        cost=None,
        target_margin_pct=None,
        margin_basis=None,
        min_margin_pct=None,
        current_margin_pct=None,
        current_margin_with_support_pct=None,
        min_price=None,
        problems=(vocabulary.LOCAL_PRODUCT,),
        origin=vocabulary.LOCAL,
    )
    return reprice(line, new_price)


def local_sku(lines: Sequence[QuotationLine]) -> str:
    """The next ``LOC-<n>`` of a quotation: one more than the highest it has used."""

    numbers = [
        int(match.group(1))
        for line in lines
        if line.is_local and (match := _LOCAL_SKU.fullmatch(line.sku))
    ]
    return f"{vocabulary.LOCAL_SKU_PREFIX}{max(numbers, default=0) + 1}"


def is_local_sku(sku: str) -> bool:
    """Whether a SKU has the shape of a local product (``LOC-<n>``), never a SIESA code."""

    return _LOCAL_SKU.fullmatch(sku.strip()) is not None


def reprice(line: QuotationLine, new_price: Decimal | None) -> QuotationLine:
    """The line with the advisor's price (``None`` clears it) and what it implies."""

    if new_price is None:
        return replace(
            line,
            new_price=None,
            new_margin_pct=None,
            new_margin_with_support_pct=None,
            variation_pct=None,
            compliance=None,
        )
    price = money(new_price)
    priced = replace(
        line,
        new_price=price,
        new_margin_pct=margin(price, line.cost),
        new_margin_with_support_pct=margin(price, line.cost, line.support_amount),
        variation_pct=variation(price, line.last_price),
    )
    return replace(priced, compliance=compliance(priced))


def price_with_taxes(line: QuotationLine) -> Decimal | None:
    """New price (else last price) plus the liquor tax, for display. VAT is not added."""

    base = _first(line.new_price, line.last_price)
    if base is None:
        return None
    return money(base + (line.value_tax_amount or ZERO))


def exported(lines: Sequence[QuotationLine]) -> list[QuotationLine]:
    """Lines that go to the files: in SIESA, active and with a new price above zero.

    A local product never goes: SIESA does not have it. So it neither counts as changed nor
    blocks the confirmation.
    """

    return [
        line
        for line in lines
        if not line.is_local
        and line.is_active
        and line.new_price is not None
        and line.new_price > 0
    ]


def counts(lines: Sequence[QuotationLine]) -> tuple[int, int, int, int]:
    """Lines, lines that go to the files, of those the ones that break the rule, and lines
    whose last price is under the minimum margin."""

    changed = exported(lines)
    blocked = [line for line in changed if line.compliance != vocabulary.MEETS]
    below = [line for line in lines if line.below_target]
    return len(lines), len(changed), len(blocked), len(below)


__all__ = [
    "build_line",
    "compliance",
    "counts",
    "customer_context",
    "exported",
    "is_local_sku",
    "local_line",
    "local_sku",
    "margin",
    "minimum_margin",
    "minimum_price",
    "pick_support",
    "price_at_margin",
    "price_for_margin",
    "price_with_taxes",
    "reprice",
    "variation",
]
