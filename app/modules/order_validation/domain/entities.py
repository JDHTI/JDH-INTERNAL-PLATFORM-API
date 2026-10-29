"""Entities of the retained orders validator."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class OrderLine:
    """One row of a retained orders file, already cleaned.

    ``margin_pct`` is a fraction (0.0222 = 2,22 %); the file writes percentage points.
    ``problems`` lists what could not be read, so a bad cell never drops the row.
    """

    row_number: int
    document: str
    customer_nit: str | None = None
    sku: str | None = None
    supplier_code: str | None = None
    supplier_name: str | None = None
    unit_price: Decimal | None = None
    quantity: Decimal | None = None
    margin_pct: Decimal | None = None
    order_date: date | None = None
    operation_center: str | None = None
    status_text: str | None = None
    customer_name: str | None = None
    salesperson: str | None = None
    channel: str | None = None
    warehouse: str | None = None
    description: str | None = None
    price_list: str | None = None
    retained: str | None = None
    # `Vlr. imp. IMPTOLIC`: the liquor tax the order carries per unit; `None` when the file
    # has no such column or cell.
    liquor_tax_value: Decimal | None = None
    problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class Agreement:
    """The part of a supplier support the validator needs."""

    id: UUID
    supplier_code: str | None
    kind: str
    calc_method: str
    status: str
    program: str | None = None
    customer_nit: str | None = None
    product_sku: str | None = None
    regionals: tuple[str, ...] = ()
    business_units: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    warehouse: str | None = None
    supplier_value: Decimal | None = None
    target_margin_pct: Decimal | None = None
    min_margin_pct: Decimal | None = None
    list_margin_pct: Decimal | None = None
    reference_cost: Decimal | None = None
    reference_list_price: Decimal | None = None
    min_quantity: Decimal | None = None
    valid_from: date | None = None
    valid_to: date | None = None


@dataclass(frozen=True)
class ProductInfo:
    """What the validator needs from a product: its margin by channel and its liquor tax.

    ``margins`` is keyed by the product margin column (``margin_list``...); a channel with
    no margin defined is ``None``.
    """

    sku: str
    margins: dict[str, Decimal | None] = field(default_factory=dict)
    liquor_tax: Decimal | None = None


@dataclass(frozen=True)
class CustomerScope:
    """Channel, business unit and regional of a client, across all its branches.

    The regional of a client is its billing center (``ANTIOQUIA``, ``OCCIDENTE``...), the
    same vocabulary the supports use.
    """

    nit: str
    channels: frozenset[str] = frozenset()
    business_units: frozenset[str] = frozenset()
    regionals: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AgreementCheck:
    """One agreement checked against one line."""

    support_id: UUID
    kind: str
    calc_method: str
    program: str | None
    valid_from: date | None
    valid_to: date | None
    needs_review: bool
    outcome: str
    detail: str
    effective_margin_pct: Decimal | None = None
    required_margin_pct: Decimal | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON form stored with the line."""

        return {
            "support_id": str(self.support_id),
            "kind": self.kind,
            "calc_method": self.calc_method,
            "program": self.program,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "needs_review": self.needs_review,
            "outcome": self.outcome,
            "detail": self.detail,
            "effective_margin_pct": _text(self.effective_margin_pct),
            "required_margin_pct": _text(self.required_margin_pct),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AgreementCheck:
        """Read the stored JSON form back."""

        return cls(
            support_id=UUID(value["support_id"]),
            kind=value["kind"],
            calc_method=value["calc_method"],
            program=value.get("program"),
            valid_from=_day(value.get("valid_from")),
            valid_to=_day(value.get("valid_to")),
            needs_review=bool(value.get("needs_review")),
            outcome=value["outcome"],
            detail=value.get("detail") or "",
            effective_margin_pct=_number(value.get("effective_margin_pct")),
            required_margin_pct=_number(value.get("required_margin_pct")),
        )


@dataclass(frozen=True)
class LineResult:
    """The decision on one line and the agreements that led to it."""

    status: str
    reason: str
    detail: str
    support_id: UUID | None = None
    effective_margin_pct: Decimal | None = None
    required_margin_pct: Decimal | None = None
    checks: tuple[AgreementCheck, ...] = ()
    # Which product margin the line was compared with: lista, mayoristas, salsamentarias.
    margin_basis: str | None = None
    # The product has liquor tax and the order may have been keyed without it.
    liquor_tax: bool = False
    liquor_tax_amount: Decimal | None = None


@dataclass(frozen=True)
class OrderPolicy:
    """Order-level release rules the business tunes (fractions: 0.18 = 18 %, 0.04 = 4 points).

    - ``max_points_below_target``: a line further than this below its target holds the whole
      order back, whatever its margin.
    - ``tolerance_min_order_margin`` and ``tolerance_max_lines``: an order whose margin reaches
      the minimum is released with up to that many lines below their target (each within
      ``max_points_below_target``).
    """

    max_points_below_target: Decimal = Decimal("0.04")
    tolerance_min_order_margin: Decimal = Decimal("0.18")
    tolerance_max_lines: int = 2


@dataclass(frozen=True)
class OrderAlert:
    """A warning on an order, with the lines behind it."""

    code: str
    message: str
    row_numbers: tuple[int, ...] = ()


@dataclass(frozen=True)
class StoredLine:
    """A line of a batch with its latest result."""

    id: UUID
    line: OrderLine
    result: LineResult


@dataclass(frozen=True)
class Batch:
    """An uploaded file and the counts of its latest validation."""

    id: UUID
    file_name: str
    uploaded_by: str
    uploaded_at: datetime
    validated_by: str
    validated_at: datetime
    line_count: int
    order_count: int
    ready_line_count: int
    ready_order_count: int
    # Margin points a line may fall short by and still be released, as a fraction.
    margin_threshold: Decimal = Decimal(0)

    @property
    def pending_line_count(self) -> int:
        """Lines still waiting for a person."""

        return self.line_count - self.ready_line_count

    @property
    def pending_order_count(self) -> int:
        """Orders still waiting for a person."""

        return self.order_count - self.ready_order_count


@dataclass(frozen=True)
class BatchPage:
    """A page of batches, newest first."""

    items: list[Batch]
    total: int
    page: int
    size: int


@dataclass(frozen=True)
class ManualApproval:
    """A person released a pending order by hand; its lines keep their own result."""

    document: str
    approved_by: str
    approved_at: datetime
    note: str | None = None


@dataclass(frozen=True)
class OrderView:
    """The lines of one document, with the order-level status."""

    document: str
    status: str
    lines: list[StoredLine]
    total_amount: Decimal | None = None
    manual_approval: ManualApproval | None = None
    # Weighted by amount over the lines that carry a cost, and the weighted target.
    margin_pct: Decimal | None = None
    required_margin_pct: Decimal | None = None
    # The order margin plus the margin threshold, when the threshold is above zero.
    margin_with_threshold_pct: Decimal | None = None
    # Why the order is ready: `lineas`, `umbral` or `manual`; `None` while pending.
    release_basis: str | None = None
    # Released by its margin, but only thanks to the threshold: the margin alone fell short.
    released_by_threshold: bool = False
    has_liquor_tax: bool = False
    # Warnings for a person: a 100 % margin product, a product far below its target...
    alerts: tuple[OrderAlert, ...] = ()
    # Per line id: the margin it brings to the order and its alert codes.
    line_margins: dict[UUID, Decimal | None] = field(default_factory=dict)
    line_alerts: dict[UUID, tuple[str, ...]] = field(default_factory=dict)

    @property
    def first(self) -> OrderLine:
        """The first line, which carries the order header (client, date, channel...)."""

        return self.lines[0].line

    @property
    def without_support_line_count(self) -> int:
        """Lines no support covers."""

        return sum(1 for item in self.lines if item.result.reason == "sin_acuerdo")

    @property
    def ready_line_count(self) -> int:
        """Lines ready for release."""

        return sum(1 for item in self.lines if item.result.status == "listo_para_liberar")


@dataclass(frozen=True)
class OrderPage:
    """A page of orders of one batch."""

    items: list[OrderView]
    total: int
    page: int
    size: int
    counts: dict[str, int] = field(default_factory=dict)
    # Orders of the whole file that the threshold releases and the margin alone would not.
    threshold_released_count: int = 0


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _number(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _day(value: Any) -> date | None:
    return None if not value else date.fromisoformat(value)
