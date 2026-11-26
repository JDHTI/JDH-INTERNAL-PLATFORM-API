"""Nivel de servicio: the lines of the fulfilled sales orders, their KPIs and the ABCD classes.

The base is every line of the orders SIESA closed as «Cumplido» (``f430_ind_estado = 4``). A line
is *pending* when it was ordered and not all of it was dispatched: what the customer asked for
and never got. The service levels measure what was served out of what was asked:

- NS Pedidos: orders with no pending line out of every order.
- NS Líneas: lines with nothing pending out of every line.
- NS Cantidad: units served out of the units ordered.
- NS Valor: value served out of the value ordered (subtotal, without taxes).
- NS Total = (NS Valor + NS Cantidad + NS Líneas) × 30 % + NS Pedidos × 10 %.

The ABCD classes rank the requested value of the last closed months (``docs/nivel-servicio.md``):
products within each operation center and customers by NIT. Ordered from the largest, an item is
A while the running share of the value is at most 80 %, B up to 95 %, C up to 99 % and D after.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

ZERO = Decimal(0)
ONE = Decimal(1)
# The weights of NS Total, from the area's board.
LINE_MEASURES_WEIGHT = Decimal("0.3")
ORDERS_WEIGHT = Decimal("0.1")

PRODUCT = "producto"
CUSTOMER = "cliente"
CLASS_KINDS = (PRODUCT, CUSTOMER)
CLASSES = ("A", "B", "C", "D")

# The groupings of the dashboard. The four of the pending (reason, responsible, product and
# reason × product) only take the lines with something pending.
DIMENSIONS = (
    "co",
    "warehouse",
    "supplier",
    "seller",
    "customer",
    "responsible",
    "reason",
    "product",
    "reason_item",
    "product_class",
    "customer_class",
)
PENDING_DIMENSIONS = frozenset({"responsible", "reason", "product", "reason_item"})
LINE_SORTS = (
    "date",
    "document",
    "co",
    "sku",
    "supplier",
    "customer",
    "seller",
    "qty_pending",
    "value_pending",
    "value",
)
GROUP_SORTS = ("value", "value_pending", "qty_pending", "ns_value", "name", "key")
# A line without reason, when filtering by reason.
WITHOUT_REASON = "none"
NO_REASON_LABEL = "SIN MOTIVO"
NO_CLASS_LABEL = "SIN CLASIFICAR"
MAX_REASON_NAME = 120
MAX_RESPONSIBLE = 60
MAX_NOTE = 500


@dataclass(frozen=True)
class OrderLine:
    """A line of a fulfilled sales order, as the warehouse gives it.

    ``line_key`` is the warehouse's key of the SIESA line (``f431_rowid``): stable between loads.
    Quantities are in the item's base unit; values are the line's subtotal (gross less the line
    and global discounts, without taxes) and the share of it never dispatched.
    """

    line_key: str
    order_key: str
    document: str | None
    co: str | None
    co_name: str | None
    order_date: date
    updated_at: datetime | None
    warehouse_code: str | None
    warehouse_name: str | None
    sku: str
    description: str | None
    supplier_code: str | None
    supplier_name: str | None
    customer_key: str | None
    customer_nit: str | None
    customer_name: str | None
    customer_branch: str | None
    seller_key: str | None
    seller_name: str | None
    qty_ordered: Decimal
    qty_dispatched: Decimal
    qty_pending: Decimal
    value_ordered: Decimal
    value_pending: Decimal

    @property
    def is_pending(self) -> bool:
        return self.qty_pending > 0


@dataclass(frozen=True)
class Reason:
    """Why a line was not served («AGOTADO PROVEEDOR») and who answers for it («PROVEEDOR»)."""

    id: UUID
    code: str
    name: str
    responsible: str
    is_active: bool
    sort_order: int


@dataclass(frozen=True)
class LineRow:
    """A stored line with its reason and the classes of its product and customer."""

    line: OrderLine
    reason: Reason | None = None
    note: str | None = None
    reason_by: str | None = None
    reason_at: datetime | None = None
    product_class: str | None = None
    customer_class: str | None = None


@dataclass(frozen=True)
class LineFilters:
    """What narrows the lines; every view of the module takes the same filters."""

    since: date
    until: date
    co: str | None = None
    warehouse: str | None = None
    supplier: str | None = None
    seller: str | None = None
    customer: str | None = None
    # Reference, description or order document.
    q: str | None = None
    reason_id: UUID | None = None
    without_reason: bool = False
    responsible: str | None = None
    product_class: str | None = None
    customer_class: str | None = None
    # ``True``: only the lines with something pending; ``False``: only the served ones.
    pending: bool | None = None


@dataclass(frozen=True)
class Totals:
    """The sums behind the KPIs of a set of lines."""

    orders: int = 0
    orders_pending: int = 0
    lines: int = 0
    lines_pending: int = 0
    qty: Decimal = ZERO
    qty_pending: Decimal = ZERO
    value: Decimal = ZERO
    value_pending: Decimal = ZERO

    @property
    def value_served(self) -> Decimal:
        """The value invoiced: what was asked less what stayed pending."""

        return self.value - self.value_pending


def served_ratio(pending: Decimal | int, total: Decimal | int) -> Decimal | None:
    """What was served out of what was asked (``1 − pending / total``), between 0 and 1.

    ``None`` when nothing was asked.
    """

    total = Decimal(total)
    if total <= 0:
        return None
    ratio = ONE - Decimal(pending) / total
    return min(max(ratio, ZERO), ONE)


@dataclass(frozen=True)
class Kpis:
    """The service levels of a set of lines, as fractions (``0.995`` is 99.5 %)."""

    totals: Totals
    ns_orders: Decimal | None
    ns_lines: Decimal | None
    ns_qty: Decimal | None
    ns_value: Decimal | None
    ns_total: Decimal | None


def kpis(totals: Totals) -> Kpis:
    """The five service levels of the area's board, from unrounded sums."""

    ns_orders = served_ratio(totals.orders_pending, totals.orders)
    ns_lines = served_ratio(totals.lines_pending, totals.lines)
    ns_qty = served_ratio(totals.qty_pending, totals.qty)
    ns_value = served_ratio(totals.value_pending, totals.value)
    parts = (ns_value, ns_qty, ns_lines, ns_orders)
    total = None
    if all(part is not None for part in parts):
        total = (ns_value + ns_qty + ns_lines) * LINE_MEASURES_WEIGHT + (  # type: ignore[operator]
            ns_orders * ORDERS_WEIGHT  # type: ignore[operator]
        )
    return Kpis(totals, ns_orders, ns_lines, ns_qty, ns_value, total)


@dataclass(frozen=True)
class Group:
    """The sums of the lines that share a key (a C.O., a supplier, a reason …)."""

    key: str | None
    name: str | None
    totals: Totals
    # The second key of a pair grouping (reason × product): the product.
    detail_key: str | None = None
    detail_name: str | None = None


@dataclass(frozen=True)
class Bands:
    """The colours of a service level: green above ``green_above``, amber from ``amber_from``,
    red below (inferred from the area's board, 2026-11-26)."""

    green_above: Decimal = Decimal("0.96")
    amber_from: Decimal = Decimal("0.90")

    def __post_init__(self) -> None:
        if not ZERO < self.amber_from <= self.green_above < ONE:
            raise ValueError("Bands must satisfy 0 < amber_from <= green_above < 1")

    @classmethod
    def parse(cls, text: str) -> Bands:
        """``"0.96,0.90"`` → green above 96 %, amber from 90 %.

        Raises:
            ValueError: Not two fractions, the green one first.
        """

        parts = [Decimal(part.strip()) for part in text.split(",") if part.strip()]
        if len(parts) != 2:
            raise ValueError("Service level bands need two fractions, e.g. 0.96,0.90")
        return cls(*parts)

    def of(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        if value > self.green_above:
            return "verde"
        return "ambar" if value >= self.amber_from else "rojo"


# --- ABCD ---


@dataclass(frozen=True)
class Cuts:
    """The running shares that close classes A, B and C; the rest is D."""

    a: Decimal = Decimal("0.80")
    b: Decimal = Decimal("0.95")
    c: Decimal = Decimal("0.99")

    def __post_init__(self) -> None:
        if not ZERO < self.a < self.b < self.c < ONE:
            raise ValueError("ABCD cuts must satisfy 0 < A < B < C < 1")

    @classmethod
    def parse(cls, text: str) -> Cuts:
        """``"0.80,0.95,0.99"`` → the cuts.

        Raises:
            ValueError: Not three ascending fractions.
        """

        parts = [Decimal(part.strip()) for part in text.split(",") if part.strip()]
        if len(parts) != 3:
            raise ValueError("ABCD cuts need three fractions, e.g. 0.80,0.95,0.99")
        return cls(*parts)

    def of(self, cumulative: Decimal) -> str:
        if cumulative <= self.a:
            return "A"
        if cumulative <= self.b:
            return "B"
        return "C" if cumulative <= self.c else "D"


@dataclass(frozen=True)
class ClassValue:
    """The requested value of an item in the window: a product in a C.O., or a customer's NIT
    (``co`` empty)."""

    co: str
    key: str
    value: Decimal


@dataclass(frozen=True)
class Classified:
    co: str
    key: str
    value: Decimal
    share: Decimal
    cumulative: Decimal
    klass: str


def classify(values: Iterable[ClassValue], cuts: Cuts) -> list[Classified]:
    """The ABCD class of each item, ranked within its C.O. (customers share one, empty).

    Items without a positive value are left out: they asked for nothing.
    """

    by_co: dict[str, list[ClassValue]] = defaultdict(list)
    for item in values:
        if item.value > 0:
            by_co[item.co].append(item)
    result: list[Classified] = []
    for co in sorted(by_co):
        items = sorted(by_co[co], key=lambda item: (-item.value, item.key))
        total = sum((item.value for item in items), ZERO)
        running = ZERO
        for item in items:
            running += item.value
            share = item.value / total
            cumulative = min(running / total, ONE)
            result.append(
                Classified(co, item.key, item.value, share, cumulative, cuts.of(cumulative))
            )
    return result


def class_counts(items: Iterable[Classified]) -> dict[str, int]:
    counts = dict.fromkeys(CLASSES, 0)
    for item in items:
        counts[item.klass] += 1
    return counts


@dataclass(frozen=True)
class ClassRun:
    """One calculation of the classes: the window it read and how many fell in each."""

    id: UUID
    period_from: date
    period_to: date
    computed_at: datetime
    computed_by: str | None
    counts: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class ParetoPoint:
    """``items`` of the items, the largest first, ask for ``value`` of the value (fractions)."""

    items: Decimal
    value: Decimal


def pareto(values: Sequence[Decimal], points: int = 100) -> list[ParetoPoint]:
    """The Pareto curve of some values, sampled to about ``points`` points (from 0, 0)."""

    ordered = sorted((value for value in values if value > 0), reverse=True)
    if not ordered:
        return []
    total = sum(ordered, ZERO)
    count = len(ordered)
    step = max(1, count // points)
    curve = [ParetoPoint(ZERO, ZERO)]
    running = ZERO
    for index, value in enumerate(ordered, start=1):
        running += value
        if index % step == 0 or index == count:
            curve.append(ParetoPoint(Decimal(index) / count, min(running / total, ONE)))
    return curve


# --- Periods ---


def month_start(day: date) -> date:
    return day.replace(day=1)


def add_months(day: date, months: int) -> date:
    """The first day of the month ``months`` away from ``day``'s month."""

    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def class_window(today: date, months: int) -> tuple[date, date]:
    """The closed months the classes read: the ``months`` before the current one."""

    start = add_months(today, -months)
    return start, month_start(today) - timedelta(days=1)


def month_chunks(since: date, until: date) -> list[tuple[date, date]]:
    """The days split by calendar month, in order."""

    chunks: list[tuple[date, date]] = []
    start = since
    while start <= until:
        end = min(add_months(start, 1) - timedelta(days=1), until)
        chunks.append((start, end))
        start = end + timedelta(days=1)
    return chunks


@dataclass(frozen=True)
class SyncReport:
    since: date | None
    until: date | None
    read: int = 0
    upserted: int = 0
    removed: int = 0
    voided: int = 0
    classes: dict[str, dict[str, int]] | None = None
    skipped: str | None = None

    def as_dict(self) -> dict:
        return {
            "since": self.since.isoformat() if self.since else None,
            "until": self.until.isoformat() if self.until else None,
            "read": self.read,
            "upserted": self.upserted,
            "removed": self.removed,
            "voided": self.voided,
            "classes": self.classes,
            "skipped": self.skipped,
        }


__all__ = [
    "CLASSES",
    "CLASS_KINDS",
    "CUSTOMER",
    "DIMENSIONS",
    "GROUP_SORTS",
    "LINE_SORTS",
    "NO_CLASS_LABEL",
    "NO_REASON_LABEL",
    "PENDING_DIMENSIONS",
    "PRODUCT",
    "WITHOUT_REASON",
    "Bands",
    "ClassRun",
    "ClassValue",
    "Classified",
    "Cuts",
    "Group",
    "Kpis",
    "LineFilters",
    "LineRow",
    "OrderLine",
    "ParetoPoint",
    "Reason",
    "SyncReport",
    "Totals",
    "add_months",
    "class_counts",
    "class_window",
    "classify",
    "kpis",
    "month_chunks",
    "month_start",
    "pareto",
    "served_ratio",
]
