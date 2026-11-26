"""The commercial budget by sales rep (presupuesto comercial).

- A sale belongs to the **current** sales rep of its customer branch (``consumo.clientes``,
  copied daily to ``customers.salesperson_code``), as BI's workbook does. The rep's team,
  regional and business unit are the rep master's (``consumo.vendedores``).
- An open month is recalculated with today's assignment, its comparisons too (the three
  months before and the same month last year). Closing a finished month freezes its picture:
  who had which customer, each rep's team and every figure.
- The budget (sales, margin, customers, collection) is filled field by field: a field left
  empty waits until someone fills it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

OPEN = "abierto"
CLOSED = "cerrado"
STATUSES = (OPEN, CLOSED)
STATUS_LABELS = {OPEN: "Abierto", CLOSED: "Cerrado"}

# The sales of customers with no rep (or not in the customer master): shown, never hidden.
NO_REP = "sin-vendedor"
NO_REP_NAME = "Sin vendedor asignado"

BUDGET_FIELDS = ("budget_sales", "budget_margin", "budget_customers", "budget_collection")
BUDGET_FIELD_LABELS = {
    "budget_sales": "Presupuesto de venta",
    "budget_margin": "Presupuesto de margen",
    "budget_customers": "Presupuesto de clientes",
    "budget_collection": "Presupuesto de recaudo",
}

# Board groupings and filters.
GROUPINGS = ("regional", "business_unit", "team")

MANUAL = "manual"
FILE = "archivo"


@dataclass(frozen=True)
class SalesRep:
    """A sales rep of the master (``consumo.vendedores``)."""

    code: str
    name: str | None
    team: str | None = None
    team_class: str | None = None
    regional: str | None = None
    business_unit: str | None = None
    is_active: bool = True


@dataclass(frozen=True)
class CustomerMonth:
    """A customer branch's sales of one month: net sale before VAT, cost and lines."""

    customer_key: str
    net: Decimal
    cost: Decimal
    lines: int = 0


@dataclass(frozen=True)
class RepMonth:
    """A rep's figures in one month: sale, cost and customers with a positive net sale.
    ``code`` is :data:`NO_REP` for the sales of customers with no rep."""

    period: str
    code: str
    name: str | None
    net: Decimal
    cost: Decimal
    customers: int


@dataclass(frozen=True)
class Assignment:
    """A customer branch's current sales rep (``None`` when it has none)."""

    customer_key: str
    salesperson_code: str | None
    salesperson_name: str | None = None


@dataclass(frozen=True)
class Budget:
    """The budget of one rep in one month; each field is optional."""

    period: str
    salesperson_code: str
    budget_sales: Decimal | None = None
    budget_margin: Decimal | None = None
    budget_customers: int | None = None
    budget_collection: Decimal | None = None
    version: int = 1
    updated_at: datetime | None = None
    updated_by: str | None = None

    def values(self) -> dict[str, Decimal | int | None]:
        return {name: getattr(self, name) for name in BUDGET_FIELDS}

    @property
    def is_empty(self) -> bool:
        return all(value is None for value in self.values().values())


@dataclass(frozen=True)
class Figures:
    """Sales, margin (not recomposed) and customers of a cut.

    ``customers`` is a whole number in a month and may be fractional in the average.
    ``margin`` is ``None`` without sales.
    """

    net: Decimal = Decimal(0)
    margin: Decimal | None = None
    customers: Decimal = Decimal(0)
    # Months with sales behind an average (``None`` for a single month).
    months: int | None = None

    @property
    def has_sales(self) -> bool:
        return self.net != 0 or self.customers != 0


EMPTY = Figures()


@dataclass(frozen=True)
class RepRow:
    """A sales rep in a month's board: the three cuts and the budget."""

    code: str
    name: str | None
    regional: str | None
    business_unit: str | None
    team: str | None
    team_class: str | None
    is_active: bool
    current: Figures
    average: Figures
    last_year: Figures
    budget: Budget | None = None


@dataclass(frozen=True)
class Totals:
    """Totals of several rows: sums, and margins weighted as BI's workbook does (the real
    margin by sales, the budget margin by budget)."""

    rows: int
    current: Figures
    average: Figures
    last_year: Figures
    budget_sales: Decimal | None
    budget_margin: Decimal | None
    budget_customers: int | None
    budget_collection: Decimal | None


@dataclass(frozen=True)
class BudgetPeriod:
    """A month of the budget: open (live) or closed (frozen picture)."""

    period: str
    status: str = OPEN
    closed_at: datetime | None = None
    closed_by: str | None = None
    reopened_at: datetime | None = None
    reopened_by: str | None = None
    version: int = 0


@dataclass(frozen=True)
class MonthLoad:
    """A month of sales copied from the warehouse."""

    period: str
    loaded_at: datetime
    customers: int
    lines: int
    net: Decimal


@dataclass(frozen=True)
class BudgetChange:
    """One change of a rep's budget."""

    period: str
    salesperson_code: str
    before: dict
    after: dict
    source: str
    note: str | None
    actor: str
    at: datetime


@dataclass(frozen=True)
class BudgetFileRow:
    """A row of an uploaded budget workbook. ``values`` holds only the fields with a value
    (a blank cell leaves the field as it is)."""

    row: int
    code: str | None
    name: str | None
    values: dict[str, Decimal | int]
    problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class Snapshot:
    """What a closed month keeps: every row. Who had which customer is copied by the
    repository itself (the month's sales joined to the customers' current rep)."""

    rows: list[RepRow]


__all__ = [
    "BUDGET_FIELDS",
    "BUDGET_FIELD_LABELS",
    "CLOSED",
    "EMPTY",
    "FILE",
    "GROUPINGS",
    "MANUAL",
    "NO_REP",
    "NO_REP_NAME",
    "OPEN",
    "STATUSES",
    "STATUS_LABELS",
    "Assignment",
    "Budget",
    "BudgetChange",
    "BudgetFileRow",
    "BudgetPeriod",
    "CustomerMonth",
    "Figures",
    "MonthLoad",
    "RepMonth",
    "RepRow",
    "SalesRep",
    "Snapshot",
    "Totals",
]
