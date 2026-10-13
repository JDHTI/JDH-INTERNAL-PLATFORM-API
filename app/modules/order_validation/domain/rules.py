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

from app.modules.commercial_masters.domain.margins import channel_field
from app.modules.order_validation.domain import vocabulary
from app.modules.order_validation.domain.entities import (
    Agreement,
    AgreementCheck,
    CustomerScope,
    LineResult,
    OrderLine,
    ProductInfo,
)
from app.modules.supplier_supports.domain.validity import validity_on

ONE = Decimal(1)
ZERO = Decimal(0)
# Margins are compared at six decimals (the precision of the support columns), so a
# margin equal to the target on paper is not rejected by a rounding remainder.
QUANTUM = Decimal("0.000001")

APPLIES = "aplica"
OUT_OF_DATE = "fuera_de_vigencia"
OUT_OF_SCOPE = "fuera_de_alcance"

_BASIS = {
    "margin_list": "lista",
    "margin_wholesale": "mayoristas",
    "margin_salsamentarias": "salsamentarias",
}
_BASIS_LABEL = {
    "lista": "margen lista",
    "mayoristas": "margen mayoristas",
    "salsamentarias": "margen salsamentarias",
}
_AMOUNT_METHODS = ("pct_costo", "costo_neto", "valor_unidad", "precio_neto", "pct_precio_lista")


def pct(value: Decimal | None) -> str:
    """A fraction as a Spanish percentage (0.1825 -> ``18,25 %``)."""

    if value is None:
        return "—"
    return f"{value * 100:.2f} %".replace(".", ",")


def _round(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(QUANTUM)


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
    if agreement.product_sku and agreement.product_sku != line.sku:
        return OUT_OF_SCOPE
    if agreement.warehouse and _label(agreement.warehouse) != _label(line.warehouse or ""):
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


def _share_of_cost(agreement: Agreement) -> tuple[Decimal | None, str]:
    """Supplier amount as a share of the cost, and how it was obtained.

    Returns:
        tuple[Decimal | None, str]: The share, or ``None`` with the reason it is unknown.
    """

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
    if method == "valor_unidad":
        return value / cost, f"{value:,.2f} por unidad de costo"
    if method == "precio_neto":
        if agreement.target_margin_pct is None:
            return None, "el precio neto no tiene margen garantizado"
        amount = cost - value * (ONE - agreement.target_margin_pct)
        return max(ZERO, amount / cost), f"precio neto pactado {value:,.2f}"
    return None, f"método {method} sin importe por unidad"


def _required(agreement: Agreement, reference: Decimal | None) -> Decimal | None:
    """Margin the line must reach with the support: the one agreed, else the product's."""

    for value in (
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
    """The product margin for the channel of the order, and its name.

    The channel is the one in the file, else the client's in the master. Wholesale and
    subdistributor channels use the wholesale margin, salsamentarias their own, and any other
    channel list margin 1. The mass food and market margins are loaded but not applied yet.

    Returns:
        tuple[Decimal | None, str | None]: The margin (``None`` when the product has none
        for that channel) and its basis (``lista``, ``mayoristas`` or ``salsamentarias``).
    """

    channel = line.channel
    if not channel and customer and customer.channels:
        channel = sorted(customer.channels)[0]
    field_name = channel_field(channel)
    basis = _BASIS[field_name]
    if product is None:
        return None, basis
    return product.margins.get(field_name), basis


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
    if method not in _AMOUNT_METHODS:
        return _outcome(agreement, "no_evaluable", f"Método {method} no evaluable por pedido")

    share, how = _share_of_cost(agreement)
    if share is None:
        return _outcome(agreement, "no_evaluable", f"No se puede evaluar: {how}")
    required = _required(agreement, reference)
    if required is None:
        return _outcome(
            agreement,
            "no_evaluable",
            f"Apoyo de {how}, pero sin margen objetivo para comparar",
        )
    effective = margin + share * (ONE - margin)
    return _compare(agreement, effective, required, f"Margen con apoyo ({how})", "objetivo")


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
    on the margin of the whole order (``release_by_threshold``). A product with liquor tax is
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

    if line.margin_pct >= ONE:
        # A 100 % margin is a line with no cost in the order, not a profitable one.
        return LineResult(
            status=vocabulary.PENDING,
            reason="datos_incompletos",
            detail=f"Margen del pedido {pct(line.margin_pct)}: la línea no trae costo",
            effective_margin_pct=_round(line.margin_pct),
        )

    day = line.order_date or today
    scopes = [(item, scope_of(item, line, customer, day)) for item in agreements]
    applicable = [item for item, scope in scopes if scope == APPLIES]
    out_of_date = [item for item, scope in scopes if scope == OUT_OF_DATE]
    checks = tuple(check(item, line, reference) for item in applicable)
    label = _BASIS_LABEL.get(basis or "", "margen del producto")

    active_ok = [item for item in checks if item.outcome == "cumple" and not item.needs_review]
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

    in_review = [item for item in checks if item.outcome == "cumple"]
    if in_review:
        best = _closest(in_review)
        detail = f"{best.detail}, pero el apoyo está pendiente de revisión"
        return _result(vocabulary.PENDING, "acuerdo_en_revision", detail, best, checks)

    failed = [item for item in checks if item.outcome == "no_cumple"]
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


def _amount(line: OrderLine) -> Decimal | None:
    """Sales of a line that carries a cost, or `None` when it cannot be weighted."""

    if line.unit_price is None or line.quantity is None or line.margin_pct is None:
        return None
    amount = line.unit_price * line.quantity
    return None if amount <= ZERO or line.margin_pct >= ONE else amount


def order_margins(
    lines: Iterable[tuple[OrderLine, LineResult]],
) -> tuple[Decimal | None, Decimal | None]:
    """General margin of an order and its target, weighted by amount.

    ``1 - sum(cost) / sum(sales)`` with each line's cost taken as ``price x quantity x
    (1 - margin)`` is the amount-weighted average of the line margins. Lines with no price,
    quantity or margin, and lines that carry no cost (100 % margin), are left out. The target
    is weighted the same way over the lines that have one.

    Returns:
        tuple[Decimal | None, Decimal | None]: The order margin and the weighted target;
        ``None`` when no line allows computing them.
    """

    sales = weighted = target_sales = target_weighted = ZERO
    for line, result in lines:
        amount = _amount(line)
        if amount is None or line.margin_pct is None:
            continue
        sales += amount
        weighted += amount * line.margin_pct
        if result.required_margin_pct is not None:
            target_sales += amount
            target_weighted += amount * result.required_margin_pct
    if sales == ZERO:
        return None, None
    return (
        _round(weighted / sales),
        _round(target_weighted / target_sales) if target_sales else None,
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
RELEASE_MANUAL = "manual"


def release_by_threshold(items: Iterable[tuple[OrderLine, LineResult]], threshold: Decimal) -> bool:
    """Whether the margin of the whole order releases an order pending only for its margin.

    The threshold is margin points added to the margin of the whole order (0 is the margin
    alone, which is always checked): the order is released when
    ``order margin + threshold >= order target``, both weighted by amount (see
    :func:`order_margins`). It never rescues an order with a line that is pending for another
    reason (incomplete data, a support in review or out of date, not evaluable) or that has no
    margin to compare with: those need a person.

    Args:
        items (Iterable[tuple[OrderLine, LineResult]]): The lines of one order.
        threshold (Decimal): Margin points, as a fraction (0.005 = 0,5 points).

    Returns:
        bool: ``True`` when the order margin, with the threshold, reaches its target.
    """

    pairs = list(items)
    if threshold < ZERO or not pairs:
        return False
    for line, result in pairs:
        if result.liquor_tax:
            # Missing liquor tax: no margin makes up for it, only a person can release it.
            return False
        if result.status == vocabulary.READY:
            continue
        if result.reason not in _SHORT_REASONS or result.required_margin_pct is None:
            return False
        if _amount(line) is None:
            return False
    margin, required = order_margins(pairs)
    if margin is None or required is None:
        return False
    return (margin + threshold).quantize(QUANTUM) >= required.quantize(QUANTUM)


def release_basis(
    items: Sequence[tuple[OrderLine, LineResult]], approved: bool, threshold: Decimal
) -> str | None:
    """Why an order is ready: ``manual`` approval, all its ``lineas``, or its ``margen``, or not."""

    if approved:
        return RELEASE_MANUAL
    if order_status(result.status for _, result in items) == vocabulary.READY:
        return RELEASE_LINES
    if release_by_threshold(items, threshold):
        return RELEASE_ORDER_MARGIN
    return None


def summarize_batch(
    items: Iterable[tuple[OrderLine, LineResult]],
    approved: Collection[str] = (),
    threshold: Decimal = ZERO,
) -> tuple[int, int, int, int]:
    """Counts of a batch: lines, orders, ready lines and ready orders.

    An order is ready when all its lines are, when a person approved it, or when the margin
    threshold releases it. Its lines keep their own status either way.
    """

    orders: dict[str, list[tuple[OrderLine, LineResult]]] = {}
    for line, result in items:
        orders.setdefault(line.document, []).append((line, result))
    line_count = sum(len(pairs) for pairs in orders.values())
    ready_lines = sum(
        1 for pairs in orders.values() for _, result in pairs if result.status == vocabulary.READY
    )
    ready_orders = sum(
        1
        for document, pairs in orders.items()
        if release_basis(pairs, document in approved, threshold) is not None
    )
    return line_count, len(orders), ready_lines, ready_orders
