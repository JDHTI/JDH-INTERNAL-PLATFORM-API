"""Price increases the suppliers send (the «MAESTRA» workbook or a short form).

A supplier fills its new cost per reference; the platform keeps the request **in review**
until someone with ``alzas-proveedor: manage`` approves or rejects it. Approving writes one row
per accepted line into the history of increases (``product_price_increases``) without a SIESA
date, so the quoter takes it as a pending increase.

The costs follow the area's workbook:

- the cost **with** the healthy-food tax is the cost without it times ``1 + ICUI`` plus the IBUA
  per unit;
- the variation compares the new cost with tax against the old one with tax;
- the increase is ``SUBIO``, ``BAJO`` or ``ESTABLE`` by its sign.

A cost differing by regional does not repeat the item (decision of 2026-11-23): the item keeps
its «Descuento Regular» and one discount column per regional; an empty one takes the regular.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

UP = "subio"
DOWN = "bajo"
FLAT = "estable"
CHANGE_LABELS = {UP: "SUBIO", DOWN: "BAJO", FLAT: "ESTABLE"}

IN_REVIEW = "en_revision"
APPROVED = "aprobada"
REJECTED = "rechazada"
REQUEST_STATUSES = (IN_REVIEW, APPROVED, REJECTED)
REQUEST_STATUS_LABELS = {IN_REVIEW: "En revisión", APPROVED: "Aprobada", REJECTED: "Rechazada"}

FROM_TEMPLATE = "plantilla"
FROM_FORM = "manual"
SOURCES = (FROM_TEMPLATE, FROM_FORM)

# The regionals of the area's workbook, with the operation center each one stands for.
REGIONALS: tuple[tuple[str, str], ...] = (
    ("ANTIOQUIA", "001"),
    ("BOGOTA", "003"),
    ("PEREIRA", "002"),
    ("BUCARAMANGA", "005"),
    ("CARTAGENA", "004"),
    ("BARRANQUILLA", "009"),
)
REGIONAL_NAMES = tuple(name for name, _ in REGIONALS)
# What the supplier may write in «OBSERVACIONES» (the workbook's list).
OBSERVATIONS = ("DESCONTINUADO", "CAMBIO_DE_REFERENCIA", "DUPLICADO")

MAX_LINES = 2000
MAX_NOTE = 500
_CENT = Decimal("0.01")


def with_tax(cost: Decimal | None, icui: Decimal | None, ibua: Decimal | None) -> Decimal | None:
    """``cost × (1 + ICUI) + IBUA``, as the workbook works it out; ``None`` without a cost."""

    if cost is None:
        return None
    return cost * (1 + (icui or 0)) + (ibua or 0)


def change_of(old: Decimal | None, new: Decimal | None) -> str | None:
    """Up, down or flat; ``None`` without both costs."""

    if old is None or new is None:
        return None
    if new > old:
        return UP
    if new < old:
        return DOWN
    return FLAT


def as_fraction(value: Decimal | None) -> Decimal | None:
    """A percentage as a fraction: ``0.2`` stays, ``20`` (points) is ``0.2``."""

    if value is None:
        return None
    return value / 100 if abs(value) > 1 else value


def list_price(cost: Decimal | None, margin: Decimal | None) -> Decimal | None:
    """The list price of a cost at a margin: ``cost / (1 - margin)``, to the cent."""

    if cost is None or margin is None or not 0 <= margin < 1:
        return None
    return (cost / (1 - margin)).quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class SupplierItem:
    """An active product of a commercial supplier, as the workbook and the form offer it."""

    sku: str
    supplier_sku: str | None
    ean: str | None
    nit: str | None
    supplier_label: str | None
    description: str | None
    pack_factor: Decimal | None
    state: str
    category: str | None
    tax_group: str | None
    # The healthy-food tax as a fraction (0.2) and the IBUA per unit, from SIESA.
    icui_pct: Decimal | None
    ibua: Decimal | None
    # The quotation in force, before discounts: without and with the healthy-food tax.
    old_cost: Decimal | None
    old_cost_with_tax: Decimal | None


@dataclass(frozen=True)
class LineInput:
    """What the supplier says for one reference."""

    sku: str
    new_cost: Decimal
    increased_on: date | None = None
    supplier_sku: str | None = None
    ean: str | None = None
    icui_pct: Decimal | None = None
    ibua: Decimal | None = None
    regular_discount_pct: Decimal | None = None
    regional_discounts: dict[str, Decimal] = field(default_factory=dict)
    observation: str | None = None
    accepted: bool = True
    row_number: int | None = None


@dataclass(frozen=True)
class RequestLine:
    """A line of a request: the item as it was and the increase the supplier sent."""

    id: UUID
    row_number: int | None
    sku: str
    supplier_sku: str | None
    ean: str | None
    description: str | None
    pack_factor: Decimal | None
    category: str | None
    tax_group: str | None
    icui_pct: Decimal | None
    ibua: Decimal | None
    old_cost: Decimal | None
    old_cost_with_tax: Decimal | None
    new_cost: Decimal
    new_cost_with_tax: Decimal
    variation_amount: Decimal | None
    variation_pct: Decimal | None
    change: str | None
    increased_on: date | None
    regular_discount_pct: Decimal | None
    regional_discounts: dict[str, Decimal]
    observation: str | None
    accepted: bool
    ledger_id: UUID | None = None

    def discount_for(self, regional: str) -> Decimal | None:
        """The discount of a regional: its own, else the regular one."""

        return self.regional_discounts.get(regional, self.regular_discount_pct)


def build_line(line_id: UUID, item: SupplierItem, values: LineInput) -> RequestLine:
    """A request line from the item and what the supplier wrote; the supplier's ICUI and IBUA
    win over SIESA's when given, and the costs with tax and the variation are worked out here
    (the workbook's formulas are never trusted)."""

    icui = values.icui_pct if values.icui_pct is not None else item.icui_pct
    ibua = values.ibua if values.ibua is not None else item.ibua
    old_with_tax = with_tax(item.old_cost, icui, ibua)
    new_with_tax = with_tax(values.new_cost, icui, ibua)
    variation = None if old_with_tax is None else new_with_tax - old_with_tax
    pct = None if variation is None or not old_with_tax else variation / old_with_tax
    return RequestLine(
        id=line_id,
        row_number=values.row_number,
        sku=item.sku,
        supplier_sku=values.supplier_sku or item.supplier_sku,
        ean=values.ean or item.ean,
        description=item.description,
        pack_factor=item.pack_factor,
        category=item.category,
        tax_group=item.tax_group,
        icui_pct=icui,
        ibua=ibua,
        old_cost=item.old_cost,
        old_cost_with_tax=old_with_tax,
        new_cost=values.new_cost,
        new_cost_with_tax=new_with_tax,
        variation_amount=variation,
        variation_pct=None if pct is None else round(pct, 6),
        change=change_of(old_with_tax, new_with_tax),
        increased_on=values.increased_on,
        regular_discount_pct=values.regular_discount_pct,
        regional_discounts=dict(values.regional_discounts),
        observation=values.observation,
        accepted=values.accepted,
    )


@dataclass(frozen=True)
class IncreaseRequest:
    """What one supplier sent at once, from the workbook or the form."""

    id: UUID
    supplier_code: str
    supplier_name: str | None
    source: str
    status: str
    file_name: str | None
    storage_key: str | None
    note: str | None
    created_by: str
    created_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    line_count: int
    accepted_count: int
    lines: tuple[RequestLine, ...] = ()


@dataclass(frozen=True)
class IncreaseRequestPage:
    items: tuple[IncreaseRequest, ...]
    total: int
    page: int
    size: int
    counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RowIssue:
    """A problem of one row of the workbook (its sheet row, header rows included)."""

    row_number: int
    sku: str | None
    problem: str


@dataclass(frozen=True)
class WorkbookRows:
    """The rows of a returned workbook that carry a new cost, and what could not be read."""

    rows_read: int
    lines: tuple[LineInput, ...]
    issues: tuple[RowIssue, ...]


@dataclass(frozen=True)
class UploadReport:
    """What a workbook would create (``applied`` false) or created."""

    applied: bool
    supplier_code: str
    rows_read: int
    lines: tuple[RequestLine, ...]
    issues: tuple[RowIssue, ...]
    request_id: UUID | None = None

    @property
    def blocked(self) -> bool:
        return bool(self.issues)


__all__ = [
    "APPROVED",
    "CHANGE_LABELS",
    "DOWN",
    "FLAT",
    "FROM_FORM",
    "FROM_TEMPLATE",
    "IN_REVIEW",
    "MAX_LINES",
    "MAX_NOTE",
    "OBSERVATIONS",
    "REGIONALS",
    "REGIONAL_NAMES",
    "REJECTED",
    "REQUEST_STATUSES",
    "REQUEST_STATUS_LABELS",
    "SOURCES",
    "UP",
    "IncreaseRequest",
    "IncreaseRequestPage",
    "LineInput",
    "RequestLine",
    "RowIssue",
    "SupplierItem",
    "UploadReport",
    "WorkbookRows",
    "as_fraction",
    "build_line",
    "change_of",
    "list_price",
    "with_tax",
]
