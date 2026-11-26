"""In-memory doubles of the commercial budget."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.core.errors import ConflictError, NotFoundError
from app.modules.commercial_budget.domain.entities import (
    BUDGET_FIELDS,
    CLOSED,
    OPEN,
    Assignment,
    Budget,
    BudgetChange,
    BudgetPeriod,
    CustomerMonth,
    MonthLoad,
    RepRow,
    SalesRep,
    Snapshot,
)

D = Decimal


def rep(code: str, name: str, team: str = "Senior Medellin", regional: str = "ANTIOQUIA", **values):
    return SalesRep(
        code=code,
        name=name,
        team=team,
        team_class=values.pop("team_class", "Senior"),
        regional=regional,
        business_unit=values.pop("business_unit", "FOOD SERVICE"),
        is_active=values.pop("is_active", True),
    )


def sold(customer: str, net: str, cost: str, lines: int = 1) -> CustomerMonth:
    return CustomerMonth(customer, D(net), D(cost), lines)


class Policy:
    def __init__(self, managers=(), readers=()) -> None:
        self.managers = set(managers)
        self.readers = set(readers)

    async def is_manager(self, principal) -> bool:
        return principal.is_platform_admin or principal.email in self.managers

    async def can_read(self, principal) -> bool:
        return await self.is_manager(principal) or principal.email in self.readers


@dataclass(frozen=True)
class Scope:
    scope: str
    codes: tuple[str, ...] | None

    def allows(self, salesperson_code: str | None) -> bool:
        if self.codes is None:
            return True
        return salesperson_code is not None and salesperson_code.strip() in self.codes


class Scopes:
    """Everyone sees every rep unless given codes."""

    def __init__(self, by_email: Mapping[str, tuple[str, ...]] | None = None) -> None:
        self.by_email = dict(by_email or {})

    async def scope_for(self, principal) -> Scope:
        codes = self.by_email.get(principal.email)
        return Scope("all", None) if codes is None else Scope("team", codes)


class FakeWarehouse:
    def __init__(self, months: Mapping[str, list[CustomerMonth]], reps: list[SalesRep]) -> None:
        self.months = dict(months)
        self.rep_list = list(reps)
        self.reads: list[str] = []

    async def month(self, start: date, following: date) -> list[CustomerMonth]:
        period = f"{start.year:04d}-{start.month:02d}"
        self.reads.append(period)
        return list(self.months.get(period, []))

    async def reps(self) -> list[SalesRep]:
        return list(self.rep_list)


class FakeRepository:
    def __init__(self, assignments: Mapping[str, Assignment] | None = None) -> None:
        self.sales_by_month: dict[str, list[CustomerMonth]] = {}
        self.load_by_month: dict[str, MonthLoad] = {}
        self.rep_map: dict[str, SalesRep] = {}
        self.assignment_map: dict[str, Assignment] = dict(assignments or {})
        self.period_map: dict[str, BudgetPeriod] = {}
        self.budget_map: dict[tuple[str, str], Budget] = {}
        self.change_list: list[BudgetChange] = []
        self.snapshots: dict[str, Snapshot] = {}

    def assign(self, customer: str, code: str | None, name: str | None = None) -> None:
        self.assignment_map[customer] = Assignment(customer, code, name)

    async def replace_month(self, period, sales, now) -> MonthLoad:
        self.sales_by_month[period] = list(sales)
        load = MonthLoad(
            period, now, len(sales), sum(i.lines for i in sales), sum((i.net for i in sales), D(0))
        )
        self.load_by_month[period] = load
        return load

    async def loads(self) -> dict[str, MonthLoad]:
        return dict(self.load_by_month)

    async def sales(self, months: Sequence[str]) -> dict[str, list[CustomerMonth]]:
        return {m: list(self.sales_by_month[m]) for m in months if m in self.sales_by_month}

    async def replace_reps(self, reps, now) -> int:
        if reps:
            self.rep_map = {item.code: item for item in reps}
        return len(reps)

    async def reps(self) -> dict[str, SalesRep]:
        return dict(self.rep_map)

    async def assignments(self) -> dict[str, Assignment]:
        return dict(self.assignment_map)

    async def period(self, period: str) -> BudgetPeriod:
        return self.period_map.get(period, BudgetPeriod(period))

    async def periods(self) -> list[BudgetPeriod]:
        return list(self.period_map.values())

    async def budgets(self, period: str) -> dict[str, Budget]:
        return {code: item for (month, code), item in self.budget_map.items() if month == period}

    async def save_budgets(
        self,
        period: str,
        changes: Mapping[str, Mapping[str, Any]],
        versions: Mapping[str, int],
        *,
        actor: str,
        source: str,
        note: str | None,
        now: datetime,
    ) -> list[Budget]:
        month = await self.period(period)
        if month.status == CLOSED:
            raise ConflictError("cerrado")
        for code, expected in versions.items():
            current = self.budget_map.get((period, code))
            if (current.version if current else 0) != expected:
                raise ConflictError("cambió")
        saved = []
        for code, values in changes.items():
            current = self.budget_map.get((period, code)) or Budget(period, code, version=0)
            before = {name: getattr(current, name) for name in values}
            updated = replace(
                current, **values, version=current.version + 1, updated_at=now, updated_by=actor
            )
            self.budget_map[(period, code)] = updated
            saved.append(updated)
            self.change_list.append(
                BudgetChange(
                    period,
                    code,
                    {k: None if v is None else str(v) for k, v in before.items()},
                    {k: None if v is None else str(v) for k, v in values.items()},
                    source,
                    note,
                    actor,
                    now,
                )
            )
        self.period_map[period] = replace(month, version=month.version + 1)
        return saved

    async def changes(self, period, salesperson_code, limit) -> list[BudgetChange]:
        found = [
            item
            for item in reversed(self.change_list)
            if item.period == period
            and (salesperson_code is None or item.salesperson_code == salesperson_code)
        ]
        return found[:limit]

    async def close(self, period, snapshot, expected_version, actor, now) -> BudgetPeriod:
        month = await self.period(period)
        if month.status == CLOSED:
            raise ConflictError("ya cerrado")
        if month.version != expected_version:
            raise ConflictError("cambió")
        self.snapshots[period] = snapshot
        closed = replace(
            month, status=CLOSED, closed_at=now, closed_by=actor, version=month.version + 1
        )
        self.period_map[period] = closed
        return closed

    async def reopen(self, period, actor, now) -> BudgetPeriod:
        month = self.period_map.get(period)
        if month is None:
            raise NotFoundError("no")
        if month.status != CLOSED:
            raise ConflictError("no cerrado")
        self.snapshots.pop(period, None)
        opened = replace(
            month, status=OPEN, reopened_at=now, reopened_by=actor, version=month.version + 1
        )
        self.period_map[period] = opened
        return opened

    async def snapshot(self, period) -> list[RepRow]:
        found = self.snapshots.get(period)
        return list(found.rows) if found else []


__all__ = [
    "BUDGET_FIELDS",
    "FakeRepository",
    "FakeWarehouse",
    "Policy",
    "Scopes",
    "rep",
    "sold",
]
