"""How a month's board is built: sales by rep, the three cuts, totals and the budget figures.

The rules follow BI's workbook (``PLANTILLA PRESUPUESTO``, September 2026):

- Sale = ``sum(subtotal)`` (net before VAT, returns subtracting, without the commercial
  discounts SKU ``NC530535``); margin = ``(sale − cost) / sale``, not recomposed.
- Customers = customer branches with a positive net sale in the month.
- The average of the three months before is the simple average of each figure over the months
  the rep sold (Excel's ``AVERAGE`` skips blank cells): sale, margin and customers.
- Totals add sales and customers; the real margin is weighted by sales and the budget margin
  by the budget (``SUMPRODUCT``), so a team's margin is never a plain average.
- ``% var. venta LY`` = budget / same month last year's sale − 1.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.modules.commercial_budget.domain import periods
from app.modules.commercial_budget.domain.entities import (
    EMPTY,
    NO_REP,
    NO_REP_NAME,
    Assignment,
    Budget,
    CustomerMonth,
    Figures,
    RepRow,
    SalesRep,
    Totals,
)

ZERO = Decimal(0)
_MARGIN = Decimal("0.000001")


def margin(net: Decimal, cost: Decimal) -> Decimal | None:
    """``(net − cost) / net``; ``None`` without sales."""

    if net == 0:
        return None
    return ((net - cost) / net).quantize(_MARGIN)


@dataclass
class _Accumulator:
    net: Decimal = ZERO
    cost: Decimal = ZERO
    customers: int = 0

    def figures(self) -> Figures:
        return Figures(self.net, margin(self.net, self.cost), Decimal(self.customers))


def rep_of(customer_key: str, assignments: Mapping[str, Assignment]) -> str:
    """The rep a customer branch's sales go to: its current rep, or :data:`NO_REP`."""

    found = assignments.get(customer_key)
    code = (found.salesperson_code or "").strip() if found else ""
    return code or NO_REP


def month_by_rep(
    sales: Iterable[CustomerMonth], assignments: Mapping[str, Assignment]
) -> dict[str, Figures]:
    """One month's figures per rep."""

    found: dict[str, _Accumulator] = defaultdict(_Accumulator)
    for item in sales:
        total = found[rep_of(item.customer_key, assignments)]
        total.net += item.net
        total.cost += item.cost
        if item.net > 0:
            total.customers += 1
    return {code: total.figures() for code, total in found.items()}


def average(months: Sequence[Figures]) -> Figures:
    """The simple average of the months the rep sold (blank months do not count)."""

    sold = [item for item in months if item.has_sales]
    if not sold:
        return Figures(months=0)
    count = Decimal(len(sold))
    margins = [item.margin for item in sold if item.margin is not None]
    return Figures(
        net=sum((item.net for item in sold), ZERO) / count,
        margin=(sum(margins, ZERO) / Decimal(len(margins))).quantize(_MARGIN) if margins else None,
        customers=sum((item.customers for item in sold), ZERO) / count,
        months=len(sold),
    )


def build_rows(
    period: str,
    *,
    sales: Mapping[str, Sequence[CustomerMonth]],
    assignments: Mapping[str, Assignment],
    reps: Mapping[str, SalesRep],
    budgets: Mapping[str, Budget],
    roster_codes: Iterable[str] = (),
) -> list[RepRow]:
    """Every rep of a month's board.

    Args:
        period (str): The month, ``YYYY-MM``.
        sales (Mapping[str, Sequence[CustomerMonth]]): Sales by customer of every month of
            :func:`periods.window` that was loaded.
        assignments (Mapping[str, Assignment]): Each customer branch's current rep.
        reps (Mapping[str, SalesRep]): The rep master, by code.
        budgets (Mapping[str, Budget]): The month's budget, by rep code.
        roster_codes (Iterable[str]): Reps listed even without sales or budget (the active
            ones of the commercial master, so their budget can be filled).

    Returns:
        list[RepRow]: One row per rep with sales in any cut, a budget or in the roster.
    """

    by_month = {
        month: month_by_rep(sales.get(month, ()), assignments) for month in periods.window(period)
    }
    previous = periods.previous_three(period)
    last_year = periods.last_year(period)
    names: dict[str, str] = {}
    for item in assignments.values():
        code = (item.salesperson_code or "").strip()
        if code and item.salesperson_name and code not in names:
            names[code] = item.salesperson_name
    codes = (
        {code for month in by_month.values() for code in month}
        | {code for code, budget in budgets.items() if not budget.is_empty}
        | {code.strip() for code in roster_codes if code and code.strip()}
    )
    rows = []
    for code in codes:
        rep = reps.get(code)
        rows.append(
            RepRow(
                code=code,
                name=NO_REP_NAME if code == NO_REP else (rep.name if rep else names.get(code)),
                regional=rep.regional if rep else None,
                business_unit=rep.business_unit if rep else None,
                team=rep.team if rep else None,
                team_class=rep.team_class if rep else None,
                is_active=rep.is_active if rep else code != NO_REP,
                current=by_month[period].get(code, EMPTY),
                average=average([by_month[month].get(code, EMPTY) for month in previous]),
                last_year=by_month[last_year].get(code, EMPTY),
                budget=budgets.get(code),
            )
        )
    return sort_rows(rows)


def sort_rows(rows: Iterable[RepRow]) -> list[RepRow]:
    """Regional, team, then rep name; the rows without a rep last."""

    return sorted(
        rows,
        key=lambda row: (
            row.code == NO_REP,
            (row.regional or "~").lower(),
            (row.team or "~").lower(),
            (row.name or row.code).lower(),
        ),
    )


def _weighted(pairs: Iterable[tuple[Decimal | None, Decimal | None]]) -> Decimal | None:
    """``SUMPRODUCT(weight, value) / SUM(weight)`` over the pairs with both."""

    weight_total, product = ZERO, ZERO
    for weight, value in pairs:
        if weight is None or value is None:
            continue
        weight_total += weight
        product += weight * value
    if weight_total == 0:
        return None
    return (product / weight_total).quantize(_MARGIN)


def _sum(values: Iterable[Decimal | int | None]) -> Decimal | None:
    present = [Decimal(value) for value in values if value is not None]
    return sum(present, ZERO) if present else None


def _cut(items: Sequence[Figures]) -> Figures:
    return Figures(
        net=sum((item.net for item in items), ZERO),
        margin=_weighted((item.net, item.margin) for item in items),
        customers=sum((item.customers for item in items), ZERO),
    )


def totals(rows: Sequence[RepRow]) -> Totals:
    """Totals of rows: sums and weighted margins."""

    budgets = [row.budget for row in rows if row.budget is not None]
    customers = _sum(budget.budget_customers for budget in budgets)
    return Totals(
        rows=len(rows),
        current=_cut([row.current for row in rows]),
        average=_cut([row.average for row in rows]),
        last_year=_cut([row.last_year for row in rows]),
        budget_sales=_sum(budget.budget_sales for budget in budgets),
        budget_margin=_weighted((budget.budget_sales, budget.budget_margin) for budget in budgets),
        budget_customers=None if customers is None else int(customers),
        budget_collection=_sum(budget.budget_collection for budget in budgets),
    )


def group(rows: Sequence[RepRow], by: str) -> list[tuple[str | None, Totals]]:
    """Totals per regional, business unit or team, in name order (blank last)."""

    found: dict[str | None, list[RepRow]] = defaultdict(list)
    for row in rows:
        found[getattr(row, by)].append(row)
    return [
        (key, totals(items))
        for key, items in sorted(found.items(), key=lambda pair: (pair[0] is None, pair[0] or ""))
    ]


def variation(budget_sales: Decimal | None, last_year_net: Decimal | None) -> Decimal | None:
    """``% var. venta LY``: budget / last year's sale − 1 (``None`` without both)."""

    if budget_sales is None or not last_year_net:
        return None
    return (budget_sales / last_year_net - 1).quantize(_MARGIN)


def compliance(net: Decimal | None, budget_sales: Decimal | None) -> Decimal | None:
    """Sale / budget (``None`` without a budget)."""

    if net is None or not budget_sales:
        return None
    return (net / budget_sales).quantize(_MARGIN)


def margin_gap(real: Decimal | None, budget: Decimal | None) -> Decimal | None:
    """Real margin − budget margin, as a fraction (0.01 = one point)."""

    if real is None or budget is None:
        return None
    return (real - budget).quantize(_MARGIN)


def matches(
    row: RepRow,
    *,
    regionals: Sequence[str] = (),
    business_units: Sequence[str] = (),
    teams: Sequence[str] = (),
    salespeople: Sequence[str] = (),
    query: str | None = None,
) -> bool:
    """Whether a row passes the board's filters (each filter takes several values)."""

    if regionals and row.regional not in regionals:
        return False
    if business_units and row.business_unit not in business_units:
        return False
    if teams and row.team not in teams:
        return False
    if salespeople and row.code not in salespeople:
        return False
    if query:
        text = query.strip().lower()
        if text and text not in f"{row.code} {row.name or ''}".lower():
            return False
    return True


__all__ = [
    "average",
    "build_rows",
    "compliance",
    "group",
    "margin",
    "margin_gap",
    "matches",
    "month_by_rep",
    "rep_of",
    "sort_rows",
    "totals",
    "variation",
]
