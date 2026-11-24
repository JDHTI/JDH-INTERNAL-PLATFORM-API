"""Analysis of the price increase history, reference by reference.

The question it answers is how much a product went up in a period (the last year by
default; the last month, two years, the whole history, a calendar month or any range of
days), how often it goes up, and how its last change ended. A period is a window of days,
``since`` to ``until``, either end open. Rows are dated by when the increase takes effect
(``increased_on``): the
notification date is not used (the workbook has it mistyped in places, and it says nothing
about when the cost moved).

The workbook is typed by hand, so its rows are not a clean chain: the old cost of a row is
not always the new cost of the one before (an old cost of 7.919 after a new one of
123.355), and ``Variacion %`` sometimes holds points (16,5) instead of a fraction.
Compounding row by row multiplies every such slip, so the figures compare **costs**, on one
basis (without the healthy-food tax; with it only when a row lacks the other):

- **Change in the period** (``accumulated_pct``): the cost at the end of the period against
  the cost just before it started. The end is the new cost of the period's last row; the
  start, the new cost of the last row before the period, or the old cost of the period's
  first row when there is none. Rows after the period do not count. A slip in a row in
  between does not add up.
- **Movement** of the reference: up, down or flat by the sign of that change (flat within
  half a hundredth of a point).
- **Change of a row:** its new cost against its old cost, both on the same basis; the file's
  ``Variacion %`` only when a pair is missing.
- **Increases:** rows of the period whose state is up. **How often:** the median number of
  days between consecutive increase dates of the period (needs two).
- **Last change:** the latest row of the period, with its state and change.
- **Typical increase** of the period: the median change of its increases (a slip of
  +1.457 % would drag an average, not a median).

These are pure functions over plain rows: the service reads the rows once per data version
and keeps the result, so filtering, sorting and paging cost nothing.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median

from app.modules.commercial_masters.domain.price_increases import DOWN, FLAT, UP, status_of

ONE = Decimal(1)
# Below this change (in either direction) a reference stayed flat.
FLAT_TOLERANCE = Decimal("0.00005")
PERIODS = {"1m": 1, "12m": 12, "24m": 24, "all": None}
DEFAULT_PERIOD = "12m"
MOVEMENTS = (UP, DOWN, FLAT)
SORTS = ("accumulated", "increases", "frequency", "last_change", "sku")


# A cost of one peso or less is a placeholder, not a cost (the quoter's rule too).
MIN_COST = ONE


def _positive(value: Decimal | None) -> Decimal | None:
    return value if value is not None and value > MIN_COST else None


@dataclass(frozen=True)
class AnalysisRow:
    """What the analysis needs of one increase row and its product."""

    sku: str
    increased_on: date
    notified_on: date | None
    status: str | None
    variation_pct: Decimal | None
    old_cost: Decimal | None
    old_cost_with_tax: Decimal | None
    new_cost: Decimal | None
    new_cost_with_tax: Decimal | None
    is_pending: bool
    description: str | None = None
    supplier_name: str | None = None
    category: str | None = None
    supplier_code: str | None = None
    regional: str | None = None
    # The order of rows with the same dates, as the workbook has them.
    occurrence: int = 1

    @property
    def cost_before(self) -> Decimal | None:
        """The old cost, without the healthy-food tax when the row has it."""

        return _positive(self.old_cost) or _positive(self.old_cost_with_tax)

    @property
    def cost_after(self) -> Decimal | None:
        """The new cost, without the healthy-food tax when the row has it."""

        return _positive(self.new_cost) or _positive(self.new_cost_with_tax)

    @property
    def change(self) -> Decimal | None:
        """The row's change as a fraction: new against old cost on the same basis; the file's
        ``Variacion %`` when neither pair is complete."""

        for old, new in (
            (self.old_cost, self.new_cost),
            (self.old_cost_with_tax, self.new_cost_with_tax),
        ):
            if _positive(old) and new is not None:
                return new / old - ONE
        return self.variation_pct

    @property
    def movement(self) -> str | None:
        """The row's state: the file's, else from the costs, else from the change."""

        if self.status:
            return self.status
        by_cost = status_of(self.cost_before, self.cost_after)
        if by_cost:
            return by_cost
        change = self.change
        return None if change is None else _sign(change)


@dataclass(frozen=True)
class ReferenceAnalysis:
    """One reference over the period."""

    sku: str
    description: str | None
    supplier_code: str | None
    supplier_name: str | None
    category: str | None
    changes: int
    increases: int
    decreases: int
    start_cost: Decimal | None
    end_cost: Decimal | None
    accumulated_pct: Decimal | None
    movement: str | None
    frequency_days: int | None
    last_increased_on: date
    last_status: str | None
    last_change_pct: Decimal | None
    is_pending: bool


@dataclass(frozen=True)
class AnalysisSummary:
    """The period as a whole: what the four figures at the top say."""

    references: int
    increases: int
    typical_increase_pct: Decimal | None
    pending: int
    movements: dict[str, int]


def _sign(value: Decimal) -> str:
    if value > FLAT_TOLERANCE:
        return UP
    if value < -FLAT_TOLERANCE:
        return DOWN
    return FLAT


def period_start(period: str, today: date) -> date | None:
    """First day the period covers; ``None`` for the whole history.

    Raises:
        ValueError: An unknown period.
    """

    if period not in PERIODS:
        raise ValueError(period)
    months = PERIODS[period]
    if months is None:
        return None
    year, month = divmod(today.month - 1 - months, 12)
    year += today.year
    month += 1
    # The same day of that month, or its last day when it has fewer.
    for day in range(today.day, 0, -1):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise AssertionError  # pragma: no cover - day 1 always exists


def _ordered(rows: Iterable[AnalysisRow]) -> dict[str, list[AnalysisRow]]:
    by_sku: dict[str, list[AnalysisRow]] = defaultdict(list)
    for row in rows:
        by_sku[row.sku].append(row)
    for items in by_sku.values():
        items.sort(
            key=lambda item: (item.increased_on, item.notified_on or date.min, item.occurrence)
        )
    return by_sku


def in_period(
    rows: Iterable[AnalysisRow], since: date | None, until: date | None = None
) -> list[AnalysisRow]:
    """The rows from ``since`` to ``until``, both days included; an open end has no limit."""

    return [
        row
        for row in rows
        if (since is None or row.increased_on >= since)
        and (until is None or row.increased_on <= until)
    ]


def analyze(
    rows: Iterable[AnalysisRow], since: date | None, until: date | None = None
) -> list[ReferenceAnalysis]:
    """One analysis per reference with rows in the period (``since`` to ``until``).

    ``rows`` is the whole history: the rows before the period give the cost it started at;
    the rows after it are left out.
    """

    result: list[ReferenceAnalysis] = []
    for sku, items in _ordered(rows).items():
        if until is not None:
            items = [item for item in items if item.increased_on <= until]
        period = in_period(items, since)
        if not period:
            continue
        before = [item for item in items if since is not None and item.increased_on < since]
        start = next((item.cost_after for item in reversed(before) if item.cost_after), None)
        if start is None:
            start = next((item.cost_before for item in period if item.cost_before), None)
        end = next((item.cost_after for item in reversed(period) if item.cost_after), None)
        accumulated = end / start - ONE if start and end else None
        increase_dates = sorted({item.increased_on for item in period if item.movement == UP})
        gaps = [(b - a).days for a, b in zip(increase_dates, increase_dates[1:], strict=False)]
        last, first = period[-1], period[0]
        result.append(
            ReferenceAnalysis(
                sku=sku,
                description=first.description,
                supplier_code=first.supplier_code,
                supplier_name=first.supplier_name,
                category=first.category,
                changes=len(period),
                increases=sum(1 for item in period if item.movement == UP),
                decreases=sum(1 for item in period if item.movement == DOWN),
                start_cost=start,
                end_cost=end,
                accumulated_pct=accumulated,
                movement=None if accumulated is None else _sign(accumulated),
                frequency_days=round(median(gaps)) if gaps else None,
                last_increased_on=last.increased_on,
                last_status=last.movement,
                last_change_pct=last.change,
                is_pending=last.is_pending,
            )
        )
    return result


def summarize(
    period_rows: Sequence[AnalysisRow], analyses: Sequence[ReferenceAnalysis]
) -> AnalysisSummary:
    """The figures of the period: references with a change, increases, the typical
    (median) increase, references pending in SIESA and references by movement."""

    increases = [row.change for row in period_rows if row.movement == UP and row.change is not None]
    movements = {movement: 0 for movement in MOVEMENTS}
    for item in analyses:
        if item.movement:
            movements[item.movement] += 1
    return AnalysisSummary(
        references=len(analyses),
        increases=sum(1 for row in period_rows if row.movement == UP),
        typical_increase_pct=median(increases) if increases else None,
        pending=sum(1 for item in analyses if item.is_pending),
        movements=movements,
    )


def matches(
    item: ReferenceAnalysis,
    q: str | None,
    movement: str | None,
    supplier: str | None = None,
) -> bool:
    """The reference passes the search (reference prefix or description), the supplier (a
    commercial supplier code, picked from a list) and the movement filter."""

    if movement and item.movement != movement:
        return False
    if supplier and item.supplier_code != supplier:
        return False
    if not q:
        return True
    term = q.strip().lower()
    return item.sku.lower().startswith(term) or term in (item.description or "").lower()


def _value(item: ReferenceAnalysis, sort: str):
    return {
        "accumulated": item.accumulated_pct,
        "increases": item.increases,
        "frequency": item.frequency_days,
        "last_change": item.last_increased_on,
        "sku": item.sku,
    }[sort]


def ordered(
    items: Sequence[ReferenceAnalysis], sort: str, descending: bool
) -> list[ReferenceAnalysis]:
    """Sorted by a column, then by reference; empty values last in either direction."""

    present = sorted(
        (item for item in items if _value(item, sort) is not None), key=lambda item: item.sku
    )
    present.sort(key=lambda item: _value(item, sort), reverse=descending)
    missing = sorted(
        (item for item in items if _value(item, sort) is None), key=lambda item: item.sku
    )
    return [*present, *missing]


__all__ = [
    "DEFAULT_PERIOD",
    "MOVEMENTS",
    "PERIODS",
    "SORTS",
    "AnalysisRow",
    "AnalysisSummary",
    "ReferenceAnalysis",
    "analyze",
    "in_period",
    "matches",
    "ordered",
    "period_start",
    "summarize",
]
