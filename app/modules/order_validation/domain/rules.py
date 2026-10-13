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

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from decimal import Decimal

from app.modules.order_validation.domain import vocabulary
from app.modules.order_validation.domain.entities import (
    Agreement,
    AgreementCheck,
    CustomerScope,
    LineResult,
    OrderLine,
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


def _required(agreement: Agreement, supplier_margin: Decimal | None) -> Decimal | None:
    """Margin the line must reach with the support: the one agreed, else the list margin."""

    for value in (
        agreement.target_margin_pct,
        agreement.min_margin_pct,
        agreement.list_margin_pct,
        supplier_margin,
    ):
        if value is not None:
            return value
    return None


def check(agreement: Agreement, line: OrderLine, supplier_margin: Decimal | None) -> AgreementCheck:
    """Check one applicable agreement against one line.

    Args:
        agreement (Agreement): A support that covers the line on the order day.
        line (OrderLine): The order line; its margin and price are known.
        supplier_margin (Decimal | None): List margin of the commercial supplier.

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
            agreement, margin, agreement.min_margin_pct, "Margen del pedido", "mínimo autorizado"
        )
    if method == "puntos_margen":
        base = agreement.list_margin_pct
        if base is None:
            base = supplier_margin
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
    required = _required(agreement, supplier_margin)
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
    supplier_margin: Decimal | None,
    today: date,
) -> LineResult:
    """Release decision for one line.

    A line is ready when an active agreement covering it on the order date is met, or
    when its margin already reaches the supplier's list margin. Anything else is left
    pending for a person, with the reason.

    Args:
        line (OrderLine): The order line.
        agreements (Sequence[Agreement]): Candidate supports of the line's supplier.
        customer (CustomerScope | None): The client in the master, if known.
        supplier_margin (Decimal | None): List margin of the commercial supplier.
        today (date): Day used when the file carries no order date.

    Returns:
        LineResult: Status, reason, the margins of the deciding check and every check.
    """

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
    checks = tuple(check(item, line, supplier_margin) for item in applicable)

    active_ok = [item for item in checks if item.outcome == "cumple" and not item.needs_review]
    if active_ok:
        best = _closest(active_ok)
        return _result(vocabulary.READY, "cumple_con_apoyo", best.detail, best, checks)

    if supplier_margin is not None and line.margin_pct >= supplier_margin:
        return LineResult(
            status=vocabulary.READY,
            reason="cumple_margen_lista",
            detail=(
                f"Margen del pedido {pct(line.margin_pct)} ≥ margen lista del proveedor "
                f"{pct(supplier_margin)}"
            ),
            effective_margin_pct=_round(line.margin_pct),
            required_margin_pct=_round(supplier_margin),
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
    if supplier_margin is not None:
        detail += (
            f"; margen del pedido {pct(line.margin_pct)} < margen lista {pct(supplier_margin)}"
        )
    return LineResult(
        status=vocabulary.PENDING,
        reason="sin_acuerdo",
        detail=detail,
        effective_margin_pct=_round(line.margin_pct),
        required_margin_pct=_round(supplier_margin),
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


def summarize(lines: Iterable[tuple[str, str]]) -> tuple[int, int, int, int]:
    """Counts of a batch from ``(document, line status)`` pairs.

    Returns:
        tuple[int, int, int, int]: Lines, orders, ready lines and ready orders.
    """

    statuses: dict[str, list[str]] = {}
    for document, status in lines:
        statuses.setdefault(document, []).append(status)
    line_count = sum(len(items) for items in statuses.values())
    ready_lines = sum(items.count(vocabulary.READY) for items in statuses.values())
    ready_orders = sum(1 for items in statuses.values() if order_status(items) == vocabulary.READY)
    return line_count, len(statuses), ready_lines, ready_orders
