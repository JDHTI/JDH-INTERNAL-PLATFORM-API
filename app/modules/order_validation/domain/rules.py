"""Whether a retained order line meets a supplier support.

The file gives the price and the order margin of each line, not its cost. With the
workbook formulas (``margin = 1 - cost / price``) the cost is ``price x (1 - margin)``,
and the margin a support brings back is

    effective margin = 1 - (cost - supplier amount) / price = margin + supplier amount / price

Every per-unit method is reduced to the supplier amount as a share of the cost (``share``),
so the box/unit question of the support never meets the unit of the order: only ratios
taken inside the support itself are used. Then ``amount / price = share x (1 - margin)``.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import UUID

from app.modules.commercial_masters.domain.margins import channel_field, margin_for
from app.modules.order_validation.domain import vocabulary
from app.modules.order_validation.domain.entities import (
    Agreement,
    AgreementCheck,
    CustomerScope,
    LineResult,
    OrderAlert,
    OrderLine,
    OrderPolicy,
    ProductInfo,
)
from app.modules.supplier_supports.domain import pricing
from app.modules.supplier_supports.domain.pricing import (
    GUARANTEED_METHOD,
    guaranteed_share,
    per_unit,
    unit_amount,
)
from app.modules.supplier_supports.domain.validity import validity_on
from app.modules.supplier_supports.domain.vocabulary import PROMO_WAREHOUSES

ONE = Decimal(1)
ZERO = Decimal(0)
# Margins are compared at six decimals (the precision of the support columns), so a
# margin equal to the target on paper is not rejected by a rounding remainder.
QUANTUM = Decimal("0.000001")

APPLIES = "aplica"
OUT_OF_DATE = "fuera_de_vigencia"
OUT_OF_SCOPE = "fuera_de_alcance"
FULL_MARGIN = "margen_100"
DEFAULT_POLICY = OrderPolicy()

BASIS = {
    "margin_list": "lista",
    "margin_wholesale": "mayoristas",
    "margin_salsamentarias": "salsamentarias",
    "margin_mass_food": "masivo_food",
    "margin_market": "market",
}
_BASIS_LABEL = {
    "lista": "margen Food Service",
    "mayoristas": "margen mayoristas",
    "salsamentarias": "margen salsamentarias",
    "masivo_food": "margen Masivo Food",
    "market": "margen Market Apartadó",
}
AMOUNT_METHODS = pricing.AMOUNT_METHODS


def pct(value: Decimal | None) -> str:
    """A fraction as a Spanish percentage (0.1825 -> ``18,25 %``)."""

    if value is None:
        return "—"
    return f"{value * 100:.2f} %".replace(".", ",")


def _round(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(QUANTUM)


_NO_ID = UUID(int=0)


def whole_groups(
    items: Iterable[tuple[Agreement, bool]], own: Collection[str] = ()
) -> list[Agreement]:
    """A business group's support covers every customer of its group (2026-11-20), also
    one that joined after it was loaded.

    ``items`` pairs each support with its ``group_wide`` flag. A group's copies (one per
    customer) become one support with no NIT and no branch, which :func:`scope_of` matches
    by the customer's group, and collapse into one: the copy of a customer in ``own`` when
    there is one (its id and DWH cost), else the first.
    """

    kept: dict[Agreement, Agreement] = {}
    for item, group_wide in items:
        if not group_wide or not item.customer_group:
            kept[item] = item
            continue
        whole = replace(item, customer_nit=None, customer_branch_id=None)
        key = replace(whole, id=_NO_ID, dwh_unit_cost=None)
        if key not in kept or item.customer_nit in own:
            kept[key] = whole
    return list(kept.values())


def _label(value: str) -> str:
    return " ".join(value.upper().split())


def _same_label(left: str, right: str) -> bool:
    """Scope labels match when equal or when one is the plural/longer form of the other.

    Supports say ``RESTAURANTE`` where the client master says ``RESTAURANTES``.
    """

    first, second = _label(left), _label(right)
    return first == second or first.startswith(second) or second.startswith(first)


def _covers(allowed: Sequence[str], values: Iterable[str]) -> bool:
    return any(_same_label(item, value) for item in allowed for value in values if value)


def scope_of(
    agreement: Agreement, line: OrderLine, customer: CustomerScope | None, day: date
) -> str:
    """Whether an agreement covers a line on the order day.

    An empty scope field means "all", as in the supports master. Regional and business
    unit come from the client master (the file does not carry them); when the client is
    unknown, an agreement restricted to them does not apply.

    Returns:
        str: :data:`APPLIES`, :data:`OUT_OF_DATE` (it would cover the line on another
        day) or :data:`OUT_OF_SCOPE`.
    """

    if agreement.supplier_code and agreement.supplier_code != line.supplier_code:
        return OUT_OF_SCOPE
    if agreement.customer_nit and agreement.customer_nit != line.customer_nit:
        return OUT_OF_SCOPE
    if agreement.customer_group and not agreement.customer_nit:
        # A business group's support without a NIT covers every customer of the group.
        group = customer.business_group if customer else None
        if not group or _label(group) != _label(agreement.customer_group):
            return OUT_OF_SCOPE
    if agreement.customer_branch_id and not (
        customer and agreement.customer_branch_id in customer.branches
    ):
        # The order file carries no branch: the customer must have it.
        return OUT_OF_SCOPE
    if agreement.product_sku and agreement.product_sku != line.sku:
        return OUT_OF_SCOPE
    if agreement.warehouse and _label(agreement.warehouse) != _label(line.warehouse or ""):
        return OUT_OF_SCOPE
    if (
        not agreement.promo_warehouses
        and line.warehouse
        and _label(line.warehouse) in PROMO_WAREHOUSES
    ):
        # A promotional warehouse (group 23) goes at its own cost unless the support says so.
        return OUT_OF_SCOPE
    if agreement.channels:
        channels = {line.channel or "", *(customer.channels if customer else ())}
        if not _covers(agreement.channels, channels):
            return OUT_OF_SCOPE
    if agreement.regionals and not (customer and _covers(agreement.regionals, customer.regionals)):
        return OUT_OF_SCOPE
    if agreement.business_units and not (
        customer and _covers(agreement.business_units, customer.business_units)
    ):
        return OUT_OF_SCOPE
    if validity_on(agreement.valid_from, agreement.valid_to, day) != "vigente":
        return OUT_OF_DATE
    return APPLIES


_HOW = {
    "pct_costo": "{value} sobre el costo",
    "pct_precio_lista": "{value} sobre el precio lista",
    "valor_unidad": "{money} en costo por unidad",
    "valor_precio_lista": "{money} sobre el precio lista por unidad",
    "costo_neto": "costo apoyado {money} por unidad",
}


def share_of_cost(
    agreement: Agreement,
    cost: Decimal | None = None,
    target: Decimal | None = None,
    margin: Decimal | None = None,
) -> tuple[Decimal | None, str]:
    """Supplier amount ($ Apoyo) as a share of the cost, and how it was obtained.

    Rules v2: the amount is :func:`unit_amount` per inventory unit, with the list price at
    the customer's optimal margin (``target``: list price = cost / (1 − target)). The cost is
    the line's when it is known (the quoter), else the support's DWH cost (the validator, whose
    file prices are not in inventory units). Without either, the references stored with the
    support decide, as before.

    A % Margen Garantizado pays what is missing to reach its margin at the price sold: from
    the line's ``margin`` without the support it is ``max(0, (g − m) / (1 − m))`` of the cost
    (the margin with it is ``max(m, g)``); without a sale margin, at the list price
    (``target``). With the supplier's minimum margin ``s`` set (2026-11-23) the share is fixed,
    ``(g − s) / (1 − s)``: what it pays at its supported price ``cost / (1 − s)``, whatever
    the price sold at.

    Returns:
        tuple[Decimal | None, str]: The share, or ``None`` with the reason it is unknown.
    """

    if agreement.calc_method == GUARANTEED_METHOD:
        guaranteed = agreement.target_margin_pct
        if guaranteed is None:
            return None, "el margen garantizado no tiene margen"
        supplier_min = agreement.supplier_min_margin_pct
        if supplier_min is not None:
            share = guaranteed_share(supplier_min, guaranteed)
            if share is None:
                return None, "el margen mínimo del proveedor no es válido"
            return share, (
                f"margen garantizado {pct(guaranteed)} desde el mínimo del proveedor "
                f"{pct(supplier_min)}"
            )
        share = guaranteed_share(margin if margin is not None else target, guaranteed)
        if share is None:
            return None, "sin margen de venta ni margen objetivo para el margen garantizado"
        return share, f"margen garantizado {pct(guaranteed)}"
    value = agreement.supplier_value
    if value is None:
        return None, "el apoyo no tiene valor"
    method = agreement.calc_method
    base = cost if cost is not None and cost > 0 else agreement.dwh_unit_cost
    if base is not None and base > 0:
        listed = price_at(base, target)
        if listed is None and agreement.reference_cost and agreement.reference_list_price:
            listed = base * agreement.reference_list_price / agreement.reference_cost
        amount = unit_amount(
            method,
            value,
            cost=base,
            list_price=listed,
            value_unit=agreement.value_unit,
            pack_factor=agreement.pack_factor,
            guaranteed_margin=agreement.target_margin_pct,
        )
        if amount is not None:
            shown = per_unit(value, agreement.value_unit, agreement.pack_factor)
            how = _HOW[method].format(value=pct(value), money=f"{shown:,.2f}")
            return max(ZERO, amount / base), how
    return _share_from_references(agreement)


def price_at(cost: Decimal, margin: Decimal | None) -> Decimal | None:
    """``cost / (1 − margin)``, or ``None`` without a usable margin."""

    if margin is None or margin >= ONE:
        return None
    return cost / (ONE - margin)


def _share_from_references(agreement: Agreement) -> tuple[Decimal | None, str]:
    """The share with the cost and list price stored in the support (first templates)."""

    value, cost, listed = (
        agreement.supplier_value,
        agreement.reference_cost,
        agreement.reference_list_price,
    )
    method = agreement.calc_method
    if value is None:
        return None, "el apoyo no tiene valor"
    if method == "pct_costo":
        return value, f"{pct(value)} sobre el costo"
    if method == "pct_precio_lista":
        if cost and listed:
            return value * listed / cost, f"{pct(value)} sobre el precio lista"
        # Without references the list price is taken as the order price: a list price is
        # never below the price sold, so this can only understate the support.
        return value, f"{pct(value)} sobre el precio lista (aproximado al precio del pedido)"
    if not cost:
        return None, "el apoyo no tiene costo de referencia"
    if method == "costo_neto":
        return max(ZERO, ONE - value / cost), f"costo neto pactado {value:,.2f}"
    if method in ("valor_unidad", "valor_precio_lista"):
        return value / cost, f"{value:,.2f} por unidad de costo"
    return None, f"método {method} sin importe por unidad"


def required_margin(agreement: Agreement, reference: Decimal | None) -> Decimal | None:
    """Margin the line must reach with the support: JDH's authorized minimum when the support
    has one (rules v2), else the one agreed, else the product's optimal margin.

    For a % Margen Garantizado the one agreed is the guaranteed margin (BI, 2026-11-18): the
    supplier guarantees it at any price, so a line it covers always reaches it ("pasa
    derecho"), in the quoter and in the order validator.
    """

    for value in (
        agreement.supported_margin_pct,
        agreement.target_margin_pct,
        agreement.min_margin_pct,
        agreement.list_margin_pct,
        reference,
    ):
        if value is not None:
            return value
    return None


def points(value: Decimal) -> str:
    """A fraction of margin as Spanish percentage points (0.005 -> ``0,50``)."""

    return f"{value * 100:.2f}".replace(".", ",")


def reference_margin(
    line: OrderLine, customer: CustomerScope | None, product: ProductInfo | None
) -> tuple[Decimal | None, str | None]:
    """The product's optimal margin for the customer's business-unit list, and its name.

    A known client uses its list (operation center 200 Market Apartadó, Masivo food team,
    Mayoristas channels, else Food service: see ``customer_margin_field``). The channel in
    the file decides only when the client is not in the master.

    Returns:
        tuple[Decimal | None, str | None]: The margin (``None`` when the product has none
        on that list) and its basis (``lista``, ``mayoristas``, ``masivo_food``, ``market``).
    """

    if customer is not None and customer.margin_field:
        field_name = customer.margin_field
        if line.channel and field_name in ("margin_list", "margin_wholesale"):
            # The channel written on the order line wins over the client's majority one.
            field_name = channel_field(line.channel)
    else:
        channel = line.channel
        if not channel and customer and customer.channels:
            channel = sorted(customer.channels)[0]
        field_name = channel_field(channel)
    basis = BASIS[field_name]
    if product is None:
        return None, basis
    return margin_for(product.margins, field_name), basis


def check(
    agreement: Agreement,
    line: OrderLine,
    reference: Decimal | None,
) -> AgreementCheck:
    """Check one applicable agreement against one line.

    Args:
        agreement (Agreement): A support that covers the line on the order day.
        line (OrderLine): The order line; its margin and price are known.
        reference (Decimal | None): The product's margin for the channel of the order.

    Returns:
        AgreementCheck: ``cumple``, ``no_cumple`` or ``no_evaluable`` with the margins.
    """

    margin = line.margin_pct or ZERO
    if agreement.min_quantity is not None and (line.quantity or ZERO) < agreement.min_quantity:
        return _outcome(
            agreement,
            "no_cumple",
            f"Cantidad pedida {line.quantity or 0} menor a la mínima pactada "
            f"{agreement.min_quantity}",
        )

    method = agreement.calc_method
    if method == "pct_ventas":
        return _outcome(
            agreement,
            "no_evaluable",
            "Rebate sobre ventas: se liquida por periodo, no por pedido",
        )
    if method == "margen_minimo":
        if agreement.min_margin_pct is None:
            return _outcome(
                agreement, "no_evaluable", "La excepción no tiene margen mínimo autorizado"
            )
        return _compare(
            agreement,
            margin,
            agreement.min_margin_pct,
            "Margen del pedido",
            "mínimo autorizado",
        )
    if method == "puntos_margen":
        base = agreement.list_margin_pct
        if base is None:
            base = reference
        if base is None or agreement.supplier_value is None:
            return _outcome(
                agreement, "no_evaluable", "Sin margen lista para restar los puntos pactados"
            )
        return _compare(
            agreement,
            margin,
            base - agreement.supplier_value,
            "Margen del pedido",
            "mínimo pactado",
        )
    if method not in AMOUNT_METHODS:
        return _outcome(agreement, "no_evaluable", f"Método {method} no evaluable por pedido")

    share, how = share_of_cost(agreement, target=reference, margin=margin)
    if share is None:
        return _outcome(agreement, "no_evaluable", f"No se puede evaluar: {how}")
    required = required_margin(agreement, reference)
    if required is None:
        return _outcome(
            agreement,
            "no_evaluable",
            f"Apoyo de {how}, pero sin margen objetivo para comparar",
        )
    effective = margin + share * (ONE - margin)
    result = _compare(agreement, effective, required, f"Margen con apoyo ({how})", "objetivo")
    if share <= ZERO and method != GUARANTEED_METHOD:
        # A supported cost above today's cost (or a zero value) brings no money: checked and
        # listed, but never the reason a line is released (BI, 2026-11-18). A guaranteed margin
        # that pays nothing is still met: the line is already at or over the guarantee.
        return replace(
            result,
            no_effect=True,
            detail=f"Apoyo sin efecto: aporta $0 ({how}). {result.detail}",
        )
    return result


def _outcome(
    agreement: Agreement,
    outcome: str,
    detail: str,
    effective: Decimal | None = None,
    required: Decimal | None = None,
) -> AgreementCheck:
    return AgreementCheck(
        support_id=agreement.id,
        kind=agreement.kind,
        calc_method=agreement.calc_method,
        program=agreement.program,
        valid_from=agreement.valid_from,
        valid_to=agreement.valid_to,
        needs_review=agreement.status == "pendiente_revision",
        outcome=outcome,
        detail=detail,
        effective_margin_pct=_round(effective),
        required_margin_pct=_round(required),
    )


def _compare(
    agreement: Agreement, effective: Decimal, required: Decimal, what: str, target: str
) -> AgreementCheck:
    complies = effective.quantize(QUANTUM) >= required.quantize(QUANTUM)
    sign = "≥" if complies else "<"
    return _outcome(
        agreement,
        "cumple" if complies else "no_cumple",
        f"{what} {pct(effective)} {sign} {target} {pct(required)}",
        effective,
        required,
    )


def _gap(item: AgreementCheck) -> Decimal:
    if item.effective_margin_pct is None or item.required_margin_pct is None:
        return Decimal("-Infinity")
    return item.effective_margin_pct - item.required_margin_pct


def _closest(checks: Sequence[AgreementCheck]) -> AgreementCheck:
    """The check nearest to (or furthest above) its target; ``checks`` is never empty."""

    return max(checks, key=_gap)


def decide(
    line: OrderLine,
    agreements: Sequence[Agreement],
    customer: CustomerScope | None,
    product: ProductInfo | None,
    today: date,
) -> LineResult:
    """Release decision for one line.

    A line is ready when an active agreement covering it on the order date is met, or when
    its margin already reaches the product's margin for the channel. Anything else is left
    pending for a person, with the reason. The margin threshold does not act on lines: it acts
    on the margin of the whole order (``release_by_margin``). A product with liquor tax is
    flagged, whatever the decision, only when the line does not carry the tax: the ``Vlr. imp.
    IMPTOLIC`` of the file is empty or 0, or the file has no such column.

    Args:
        line (OrderLine): The order line.
        agreements (Sequence[Agreement]): Candidate supports of the line's supplier.
        customer (CustomerScope | None): The client in the master, if known.
        product (ProductInfo | None): The product in the master, if known.
        today (date): Day used when the file carries no order date.

    Returns:
        LineResult: Status, reason, the margins of the deciding check and every check.
    """

    reference, basis = reference_margin(line, customer, product)
    result = _decide(line, agreements, customer, reference, basis, today)
    owed = product.liquor_tax if product and product.liquor_tax and product.liquor_tax > 0 else None
    carried = line.liquor_tax_value is not None and line.liquor_tax_value > 0
    tax = owed if owed is not None and not carried else None
    result = replace(
        result,
        margin_basis=basis if reference is not None else None,
        liquor_tax=tax is not None,
        liquor_tax_amount=tax,
    )
    if tax is not None and result.status == vocabulary.READY:
        # A line that meets its margin but lacks the liquor tax is held back: the order cannot
        # go out without the tax. Lines pending for another reason keep that reason.
        return replace(
            result,
            status=vocabulary.PENDING,
            reason="falta_impuesto_licor",
            detail=(
                f"Falta el impuesto al licor: el producto lo tiene y la línea no lo trae "
                f"(debería traer {_money(tax)} por unidad). {result.detail}"
            ),
        )
    return result


def _money(value: Decimal) -> str:
    """A Colombian peso amount: 6911 becomes ``$6.911``."""

    return f"${value:,.0f}".replace(",", ".")


def _decide(
    line: OrderLine,
    agreements: Sequence[Agreement],
    customer: CustomerScope | None,
    reference: Decimal | None,
    basis: str | None,
    today: date,
) -> LineResult:
    if line.supplier_code is None or line.margin_pct is None or line.unit_price is None:
        missing = [
            name
            for name, value in (
                ("proveedor", line.supplier_code),
                ("precio", line.unit_price),
                ("margen", line.margin_pct),
            )
            if value is None
        ]
        return LineResult(
            status=vocabulary.PENDING,
            reason="datos_incompletos",
            detail="; ".join((f"Falta {', '.join(missing)} en el archivo", *line.problems)),
        )

    if is_full_margin(line):
        # A 100 % margin is a line with no cost in the order, not a profitable one, and it would
        # carry the margin of the whole order: only a person releases it (see release_basis).
        return LineResult(
            status=vocabulary.PENDING,
            reason=FULL_MARGIN,
            detail=(
                f"Margen {pct(line.margin_pct)}, revisar: la línea no trae costo. El pedido solo "
                "se libera con aprobación manual"
            ),
            effective_margin_pct=_round(line.margin_pct),
        )

    day = line.order_date or today
    scopes = [(item, scope_of(item, line, customer, day)) for item in agreements]
    applicable = [item for item, scope in scopes if scope == APPLIES]
    out_of_date = [item for item, scope in scopes if scope == OUT_OF_DATE]
    checks = tuple(check(item, line, reference) for item in applicable)
    label = _BASIS_LABEL.get(basis or "", "margen del producto")

    # A support without effect ($0) never releases a line: the line stands on its own margin.
    effective_checks = [item for item in checks if not item.no_effect]
    active_ok = [
        item for item in effective_checks if item.outcome == "cumple" and not item.needs_review
    ]
    if active_ok:
        best = _closest(active_ok)
        return _result(vocabulary.READY, "cumple_con_apoyo", best.detail, best, checks)

    if reference is not None and line.margin_pct.quantize(QUANTUM) >= reference.quantize(QUANTUM):
        return LineResult(
            status=vocabulary.READY,
            reason="cumple_margen_lista",
            detail=f"Margen del pedido {pct(line.margin_pct)} ≥ {label} {pct(reference)}",
            effective_margin_pct=_round(line.margin_pct),
            required_margin_pct=_round(reference),
            checks=checks,
        )

    in_review = [item for item in effective_checks if item.outcome == "cumple"]
    if in_review:
        best = _closest(in_review)
        detail = f"{best.detail}, pero el apoyo está pendiente de revisión"
        return _result(vocabulary.PENDING, "acuerdo_en_revision", detail, best, checks)

    # Below its own margin, a support without effect does not bring it there either.
    failed = [item for item in checks if item.outcome == "no_cumple" or item.no_effect]
    if failed:
        best = _closest(failed)
        return _result(vocabulary.PENDING, "no_cumple_acuerdo", best.detail, best, checks)

    if checks:
        first = checks[0]
        return _result(vocabulary.PENDING, "acuerdo_no_evaluable", first.detail, first, checks)

    if out_of_date:
        latest = max(out_of_date, key=lambda item: item.valid_to or item.valid_from or day)
        window = f"{latest.valid_from or 'sin inicio'} a {latest.valid_to or 'sin fin'}"
        return LineResult(
            status=vocabulary.PENDING,
            reason="acuerdo_vencido",
            detail=f"Hay un apoyo para la línea, pero rige de {window}",
            support_id=latest.id,
        )

    detail = "No hay apoyo registrado para este cliente, referencia y proveedor"
    if reference is not None:
        detail += f"; margen del pedido {pct(line.margin_pct)} < {label} {pct(reference)}"
    else:
        detail += "; el producto no tiene margen definido para este canal"
    return LineResult(
        status=vocabulary.PENDING,
        reason="sin_acuerdo",
        detail=detail,
        effective_margin_pct=_round(line.margin_pct),
        required_margin_pct=_round(reference),
    )


def _result(
    status: str,
    reason: str,
    detail: str,
    best: AgreementCheck,
    checks: tuple[AgreementCheck, ...],
) -> LineResult:
    return LineResult(
        status=status,
        reason=reason,
        detail=detail,
        support_id=best.support_id,
        effective_margin_pct=best.effective_margin_pct,
        required_margin_pct=best.required_margin_pct,
        checks=checks,
    )


def is_full_margin(line: OrderLine) -> bool:
    """Whether a line comes with a 100 % margin (or more): it carries no cost."""

    return line.margin_pct is not None and line.margin_pct >= ONE


def _amount(line: OrderLine) -> Decimal | None:
    """Sales of a line that carries a cost, or `None` when it cannot be weighted."""

    if line.unit_price is None or line.quantity is None or line.margin_pct is None:
        return None
    amount = line.unit_price * line.quantity
    return None if amount <= ZERO or is_full_margin(line) else amount


def line_margin(line: OrderLine, result: LineResult) -> Decimal | None:
    """The margin a line brings to its order: with its support when one decided it.

    The file gives each line's margin without the supplier's money. When an active support
    with money for the line (cost, net cost, per unit, net price, list price) decided it, met or
    not, the line's margin is the one with that support: a line at 1 % that the support brings
    to 22 % counts as 22 %. A support pending review, out of date or not evaluable does not
    count; neither do minimum-margin exceptions, which bring no money.
    """

    if line.margin_pct is None:
        return None
    if result.support_id is not None and result.effective_margin_pct is not None:
        deciding = next(
            (item for item in result.checks if item.support_id == result.support_id), None
        )
        if (
            deciding is not None
            and not deciding.needs_review
            and deciding.outcome in ("cumple", "no_cumple")
            and deciding.calc_method in AMOUNT_METHODS
        ):
            return result.effective_margin_pct
    return line.margin_pct


def points_below_target(line: OrderLine, result: LineResult) -> Decimal | None:
    """How far a line's margin (with its support) falls below its target; `None` when not below."""

    margin = line_margin(line, result)
    if margin is None or result.required_margin_pct is None or is_full_margin(line):
        return None
    gap = (result.required_margin_pct - margin).quantize(QUANTUM)
    return gap if gap > ZERO else None


def _far_below(line: OrderLine, result: LineResult, policy: OrderPolicy) -> bool:
    """A pending line further below its target than the policy tolerates."""

    if result.status == vocabulary.READY:
        return False
    gap = points_below_target(line, result)
    return gap is not None and gap > policy.max_points_below_target.quantize(QUANTUM)


def order_margins(
    lines: Iterable[tuple[OrderLine, LineResult]],
) -> tuple[Decimal | None, Decimal | None]:
    """General margin of an order and its target, weighted by amount.

    ``1 - sum(cost) / sum(sales)`` with each line's cost taken as ``price x quantity x
    (1 - margin)`` is the amount-weighted average of the line margins, each one with its support
    when a support decided the line (:func:`line_margin`). Lines with no price, quantity or
    margin, and lines that carry no cost (100 % margin), are left out. The target is weighted the
    same way over the lines that have one.

    Returns:
        tuple[Decimal | None, Decimal | None]: The order margin and the weighted target;
        ``None`` when no line allows computing them.
    """

    sales = weighted = target_sales = target_weighted = ZERO
    for line, result in lines:
        amount = _amount(line)
        margin = line_margin(line, result)
        if amount is None or margin is None:
            continue
        sales += amount
        weighted += amount * margin
        if result.required_margin_pct is not None:
            target_sales += amount
            target_weighted += amount * result.required_margin_pct
    if sales == ZERO:
        return None, None
    return (
        _round(weighted / sales),
        _round(target_weighted / target_sales) if target_sales else None,
    )


def line_alerts(
    line: OrderLine, result: LineResult, policy: OrderPolicy = DEFAULT_POLICY
) -> tuple[str, ...]:
    """The alert codes of one line (see :data:`vocabulary.ALERTS`)."""

    codes = []
    if is_full_margin(line):
        codes.append(FULL_MARGIN)
    if _far_below(line, result, policy):
        codes.append("bajo_objetivo")
    if result.liquor_tax:
        codes.append("falta_impuesto_licor")
    return tuple(codes)


def order_alerts(
    items: Sequence[tuple[OrderLine, LineResult]], policy: OrderPolicy = DEFAULT_POLICY
) -> tuple[OrderAlert, ...]:
    """The warnings of an order, each with the rows behind it."""

    rows: dict[str, list[int]] = {}
    for line, result in items:
        for code in line_alerts(line, result, policy):
            rows.setdefault(code, []).append(line.row_number)
    points_text = points(policy.max_points_below_target)
    messages = {
        FULL_MARGIN: (
            "Margen 100 %, revisar: hay productos sin costo; el pedido solo se libera con "
            "aprobación manual (inflarían el margen acumulado del pedido)"
        ),
        "bajo_objetivo": (
            f"Hay productos a más de {points_text} puntos por debajo de su margen objetivo: el "
            "pedido no se libera por su margen"
        ),
        "falta_impuesto_licor": (
            "Hay productos con impuesto al licor que la línea no trae: el pedido no se libera por "
            "su margen"
        ),
    }
    return tuple(
        OrderAlert(code=code, message=messages[code], row_numbers=tuple(rows[code]))
        for code in vocabulary.ALERTS
        if code in rows
    )


def line_sort_key(line: OrderLine, result: LineResult) -> tuple[int, int, Decimal, int]:
    """Lines needing attention first: no support, then margin from lowest to highest."""

    margin = line.margin_pct if line.margin_pct is not None else Decimal("Infinity")
    return (
        0 if result.reason == "sin_acuerdo" else 1,
        0 if line.margin_pct is not None else 1,
        margin,
        line.row_number,
    )


def order_status(statuses: Iterable[str]) -> str:
    """An order is ready for release only when every one of its lines is."""

    return (
        vocabulary.READY
        if all(status == vocabulary.READY for status in statuses)
        else vocabulary.PENDING
    )


def by_supplier(agreements: Iterable[Agreement]) -> Mapping[str | None, list[Agreement]]:
    """Group agreements by commercial supplier code (``None`` for supplier-less ones)."""

    grouped: dict[str | None, list[Agreement]] = {}
    for item in agreements:
        grouped.setdefault(item.supplier_code, []).append(item)
    return grouped


# Reasons of a line that is pending only because its margin falls short.
_SHORT_REASONS = ("sin_acuerdo", "no_cumple_acuerdo")
RELEASE_LINES = "lineas"
RELEASE_ORDER_MARGIN = "margen"
RELEASE_TOLERANCE = "tolerancia"
RELEASE_MANUAL = "manual"


def release_by_margin(
    items: Iterable[tuple[OrderLine, LineResult]],
    threshold: Decimal,
    policy: OrderPolicy = DEFAULT_POLICY,
) -> str | None:
    """Whether the margin of the whole order releases an order pending only for its margin.

    Two ways, both over the order margin with supports (:func:`order_margins`):

    - ``margen``: ``order margin + threshold >= order target``. The threshold is margin points
      added to the order margin (0 is the margin alone, always checked).
    - ``tolerancia``: the order margin alone reaches ``policy.tolerance_min_order_margin`` (18 %)
      and at most ``policy.tolerance_max_lines`` (2) lines are below their target.

    Neither rescues an order with a line pending for another reason (incomplete data, a support in
    review or out of date, not evaluable), with no margin to compare with, without the liquor tax
    it owes, with a 100 % margin, or with a line further than ``policy.max_points_below_target``
    (4 points) below its target: those need a person.

    Args:
        items (Iterable[tuple[OrderLine, LineResult]]): The lines of one order.
        threshold (Decimal): Margin points, as a fraction (0.005 = 0,5 points).
        policy (OrderPolicy): The order-level limits.

    Returns:
        str | None: :data:`RELEASE_ORDER_MARGIN`, :data:`RELEASE_TOLERANCE` or ``None``.
    """

    pairs = list(items)
    if threshold < ZERO or not pairs:
        return None
    short = 0
    for line, result in pairs:
        if result.liquor_tax or is_full_margin(line):
            # No margin makes up for these: only a person can release the order.
            return None
        if result.status == vocabulary.READY:
            continue
        if result.reason not in _SHORT_REASONS or result.required_margin_pct is None:
            return None
        if _amount(line) is None or _far_below(line, result, policy):
            return None
        short += 1
    margin, required = order_margins(pairs)
    if margin is None or required is None:
        return None
    if (margin + threshold).quantize(QUANTUM) >= required.quantize(QUANTUM):
        return RELEASE_ORDER_MARGIN
    if 0 < short <= policy.tolerance_max_lines and margin.quantize(
        QUANTUM
    ) >= policy.tolerance_min_order_margin.quantize(QUANTUM):
        return RELEASE_TOLERANCE
    return None


def release_basis(
    items: Sequence[tuple[OrderLine, LineResult]],
    approved: bool,
    threshold: Decimal,
    policy: OrderPolicy = DEFAULT_POLICY,
) -> str | None:
    """Why an order is ready: ``manual``, all its ``lineas``, its ``margen`` or ``tolerancia``.

    An order with a 100 % margin product is only ready with a manual approval: neither its
    lines nor its margin release it.
    """

    if approved:
        return RELEASE_MANUAL
    if any(is_full_margin(line) for line, _ in items):
        return None
    if order_status(result.status for _, result in items) == vocabulary.READY:
        return RELEASE_LINES
    return release_by_margin(items, threshold, policy)


def needs_threshold(margin: Decimal | None, required: Decimal | None) -> bool:
    """Whether an order's margin falls short of its target, so only the threshold can release it."""

    if margin is None or required is None:
        return False
    return margin.quantize(QUANTUM) < required.quantize(QUANTUM)


def summarize_batch(
    items: Iterable[tuple[OrderLine, LineResult]],
    approved: Collection[str] = (),
    threshold: Decimal = ZERO,
    policy: OrderPolicy = DEFAULT_POLICY,
) -> tuple[int, int, int, int, int]:
    """Counts of a batch: lines, orders, ready lines, ready orders and released orders.

    An order a person marked (``approved``) is released and counts only there. Any other order
    is ready when all its lines are or when its margin releases it (see :func:`release_basis`).
    Its lines keep their own status either way.
    """

    orders: dict[str, list[tuple[OrderLine, LineResult]]] = {}
    for line, result in items:
        orders.setdefault(line.document, []).append((line, result))
    line_count = sum(len(pairs) for pairs in orders.values())
    ready_lines = sum(
        1 for pairs in orders.values() for _, result in pairs if result.status == vocabulary.READY
    )
    released_orders = sum(1 for document in orders if document in approved)
    ready_orders = sum(
        1
        for document, pairs in orders.items()
        if document not in approved and release_basis(pairs, False, threshold, policy) is not None
    )
    return line_count, len(orders), ready_lines, ready_orders, released_orders
