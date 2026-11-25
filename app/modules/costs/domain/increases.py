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

Each row carries a «Regional», as in the area's workbook (decision of 2026-11-24, replacing the
discount column per regional): the row's cost, discount and PVP hold where it says.

- ``NACIONAL``: every regional. It is the only row of its reference.
- One regional (``ANTIOQUIA``): only that one. A regional without a row keeps its cost.
- ``RESTO DE REGIONALES``: every regional the other rows of the reference do not name. It
  saves repeating a cost five times when one regional differs. It needs at least one row with
  a regional and must leave some regional to cover.

On approval each row becomes one history row per regional it covers (``NACIONAL`` stays one
row), so the history keeps the area's format of one regional per row.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol
from uuid import UUID

from app.modules.commercial_masters.domain.price_increases import REGIONAL_CENTERS

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
REGIONALS: tuple[tuple[str, str], ...] = tuple(REGIONAL_CENTERS.items())
REGIONAL_NAMES = tuple(name for name, _ in REGIONALS)
NATIONAL = "NACIONAL"
REST = "RESTO DE REGIONALES"
# What the «Regional» of a row may say, in the order of the workbook's list.
SCOPES = (NATIONAL, *REGIONAL_NAMES, REST)
_SCOPE_ALIASES = {"RESTO": REST, "RESTO REGIONALES": REST, "NACIONAL": NATIONAL}
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


def clean_scope(text: str | None) -> str | None:
    """The «Regional» of a row: blank is ``NACIONAL``; accents, case and spaces do not count
    («Bogotá», «resto»). ``None`` when it is none of :data:`SCOPES`."""

    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    plain = " ".join(plain.upper().split())
    if not plain:
        return NATIONAL
    plain = _SCOPE_ALIASES.get(plain, plain)
    return plain if plain in SCOPES else None


class _Scoped(Protocol):
    @property
    def sku(self) -> str: ...

    @property
    def regional(self) -> str: ...


def covered_regionals(regional: str, siblings: Sequence[str]) -> tuple[str, ...]:
    """The regionals a row holds for, given the «Regional» of the other rows of its reference."""

    if regional == NATIONAL:
        return REGIONAL_NAMES
    if regional == REST:
        named = set(siblings)
        return tuple(name for name in REGIONAL_NAMES if name not in named)
    return (regional,)


def scope_problems(rows: Sequence[_Scoped]) -> dict[int, list[str]]:
    """What is wrong with the «Regional» of the rows, by position, looking at each reference's
    rows together.

    - a regional (or ``NACIONAL``) repeated for the same reference;
    - ``NACIONAL`` with other rows of the same reference;
    - ``RESTO DE REGIONALES`` without a row with a regional, or with the six already named.
    """

    by_sku: dict[str, list[int]] = defaultdict(list)
    for position, row in enumerate(rows):
        by_sku[row.sku].append(position)
    problems: dict[int, list[str]] = defaultdict(list)
    for positions in by_sku.values():
        scopes = [rows[position].regional for position in positions]
        seen: set[str] = set()
        named = [scope for scope in scopes if scope in REGIONAL_NAMES]
        for position, scope in zip(positions, scopes, strict=True):
            if scope in seen:
                problems[position].append(f"La regional {scope} está repetida en la referencia")
                continue
            seen.add(scope)
            if scope == NATIONAL and len(scopes) > 1:
                problems[position].append(
                    "NACIONAL no se combina con otras filas de la referencia; escriba la regional "
                    f"que cambia y {REST} para las demás"
                )
            elif scope == REST and not named:
                problems[position].append(
                    f"{REST} necesita otra fila de la referencia con una regional; si el costo "
                    "es el mismo en todas use NACIONAL"
                )
            elif scope == REST and len(set(named)) == len(REGIONAL_NAMES):
                problems[position].append(f"{REST} no cubre ninguna regional: ya están las seis")
    return dict(problems)


def with_coverage(lines: Sequence[RequestLine]) -> list[RequestLine]:
    """The lines with the regionals each one covers among the lines of its reference."""

    by_sku: dict[str, list[str]] = defaultdict(list)
    for line in lines:
        by_sku[line.sku].append(line.regional)
    return [
        replace(
            line,
            regionals=covered_regionals(
                line.regional, [scope for scope in by_sku[line.sku] if scope != line.regional]
            ),
        )
        for line in lines
    ]


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
    # ``NACIONAL``, a regional or ``RESTO DE REGIONALES`` (:data:`SCOPES`).
    regional: str = NATIONAL
    # The public sale price without VAT the supplier suggests («PVP»).
    public_price: Decimal | None = None
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
    regional: str
    public_price: Decimal | None
    observation: str | None
    accepted: bool
    # The first history row it became, once approved (``RESTO`` becomes several).
    ledger_id: UUID | None = None
    # The regionals the line holds for among the lines of its reference (:func:`with_coverage`).
    regionals: tuple[str, ...] = ()


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
        regional=values.regional,
        public_price=values.public_price,
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
    support: SupportFile | None = None
    # Approved lines whose increase SIESA already has (a confirmed load): the notice has them.
    loaded_count: int = 0


@dataclass(frozen=True)
class SupportFile:
    """The supplier's support of an increase (its letter, an email as PDF), in the bucket
    under the area."""

    file_name: str
    content_type: str
    size_bytes: int
    storage_key: str
    uploaded_by: str
    uploaded_at: datetime


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
    "NATIONAL",
    "OBSERVATIONS",
    "REGIONALS",
    "REGIONAL_NAMES",
    "REJECTED",
    "REQUEST_STATUSES",
    "REQUEST_STATUS_LABELS",
    "REST",
    "SCOPES",
    "SOURCES",
    "UP",
    "IncreaseRequest",
    "SupportFile",
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
    "clean_scope",
    "covered_regionals",
    "list_price",
    "scope_problems",
    "with_coverage",
    "with_tax",
]
