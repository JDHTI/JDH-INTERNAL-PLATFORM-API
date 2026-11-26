"""Ports of Gestión comercial."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Protocol

from app.modules.commercial_budget.domain.entities import (
    Budget,
    BudgetChange,
    BudgetPeriod,
    CustomerMonth,
    MonthLoad,
    RepMonth,
    RepRow,
    SalesRep,
    Snapshot,
)
from app.modules.identity.domain.principal import Principal


class Policy(Protocol):
    """A module permission: who manages and who reads."""

    async def is_manager(self, principal: Principal) -> bool: ...

    async def can_read(self, principal: Principal) -> bool: ...


class LeaderPolicy(Protocol):
    """Who closes and reopens a month: the leaders of BI and Comercial, and administrators."""

    async def is_manager(self, principal: Principal) -> bool: ...


class Scope(Protocol):
    """A person's sales scope: ``codes`` ``None`` means every rep."""

    codes: tuple[str, ...] | None
    scope: str

    def allows(self, salesperson_code: str | None) -> bool: ...


class ScopeResolver(Protocol):
    async def scope_for(self, principal: Principal) -> Scope: ...


class SalesWarehouse(Protocol):
    """The warehouse: a month of sales by customer branch, and the rep master."""

    async def month(self, start: date, following: date) -> list[CustomerMonth]: ...

    async def reps(self) -> list[SalesRep]: ...


class BudgetRepository(Protocol):
    """The platform's tables of the budget."""

    # sales copied from the warehouse
    async def replace_month(
        self, period: str, sales: Sequence[CustomerMonth], now: datetime
    ) -> MonthLoad: ...

    async def loads(self) -> dict[str, MonthLoad]: ...

    async def rep_months(self, months: Sequence[str]) -> list[RepMonth]: ...

    # the rep master
    async def replace_reps(self, reps: Sequence[SalesRep], now: datetime) -> int: ...

    async def reps(self) -> dict[str, SalesRep]: ...

    # months and budgets
    async def period(self, period: str) -> BudgetPeriod: ...

    async def periods(self) -> list[BudgetPeriod]: ...

    async def budgets(self, period: str) -> dict[str, Budget]: ...

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
    ) -> list[Budget]: ...

    async def changes(
        self, period: str, salesperson_code: str | None, limit: int
    ) -> list[BudgetChange]: ...

    # close
    async def close(
        self,
        period: str,
        snapshot: Snapshot,
        expected_version: int,
        actor: str,
        now: datetime,
    ) -> BudgetPeriod: ...

    async def reopen(self, period: str, actor: str, now: datetime) -> BudgetPeriod: ...

    async def snapshot(self, period: str) -> list[RepRow]: ...


__all__ = [
    "BudgetRepository",
    "LeaderPolicy",
    "Policy",
    "SalesWarehouse",
    "Scope",
    "ScopeResolver",
]
