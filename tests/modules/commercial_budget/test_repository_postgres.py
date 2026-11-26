"""Opt-in: the budget repository on PostgreSQL, migrated to head (0093).

Runs with ``COMMERCIAL_BUDGET_TEST_DATABASE_URL`` (never the shared database: it replaces
``sales_reps``).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.core.errors import ConflictError
from app.infrastructure.database import Database
from app.modules.commercial_budget.domain import rules
from app.modules.commercial_budget.domain.entities import CLOSED, FILE, MANUAL, OPEN, Assignment
from app.modules.commercial_budget.domain.entities import Snapshot as Picture
from app.modules.commercial_budget.infrastructure.models import (
    SalesBudgetChangeModel,
    SalesBudgetModel,
    SalesBudgetPeriodModel,
    SalesBudgetSnapshotCustomerModel,
    SalesBudgetSnapshotRepModel,
    SalesMonthCustomerModel,
    SalesMonthLoadModel,
)
from app.modules.commercial_budget.infrastructure.repository import SqlAlchemyBudgetRepository
from tests.modules.commercial_budget.fakes import rep, sold

pytestmark = pytest.mark.skipif(
    not os.getenv("COMMERCIAL_BUDGET_TEST_DATABASE_URL"),
    reason="Dedicated PostgreSQL integration URL not set",
)

# Months no real budget uses.
PERIOD, BEFORE = "2001-03", "2001-02"
NOW = datetime(2001, 4, 2, tzinfo=UTC)
D = Decimal


@pytest.fixture
async def repository():
    database = Database(os.environ["COMMERCIAL_BUDGET_TEST_DATABASE_URL"])
    try:
        yield SqlAlchemyBudgetRepository(database)
    finally:
        async with database.session_factory() as session, session.begin():
            for model in (
                SalesMonthCustomerModel,
                SalesMonthLoadModel,
                SalesBudgetModel,
                SalesBudgetChangeModel,
                SalesBudgetPeriodModel,
                SalesBudgetSnapshotRepModel,
                SalesBudgetSnapshotCustomerModel,
            ):
                await session.execute(delete(model).where(model.period.in_([PERIOD, BEFORE])))
        await database.dispose()


async def test_months_budgets_close_and_reopen(repository) -> None:
    await repository.replace_month(PERIOD, [sold("c1", "10", "8"), sold("c2", "5", "4")], NOW)
    load = await repository.replace_month(PERIOD, [sold("c1", "12", "9", 3)], NOW)
    await repository.replace_month(BEFORE, [sold("c1", "7", "6")], NOW)
    assert (load.customers, load.lines, load.net) == (1, 3, D(12))
    assert (await repository.sales([PERIOD]))[PERIOD][0].net == D(12)
    assert {PERIOD, BEFORE} <= set(await repository.loads())

    assert await repository.replace_reps([rep("101", "Ana"), rep("102", "Luis")], NOW) == 2
    assert set(await repository.reps()) == {"101", "102"}

    first = await repository.save_budgets(
        PERIOD,
        {"101": {"budget_sales": D(1000)}},
        {},
        actor="bi",
        source=MANUAL,
        note=None,
        now=NOW,
    )
    second = await repository.save_budgets(
        PERIOD,
        {"101": {"budget_margin": D("0.18")}, "102": {"budget_customers": 12}},
        {"101": 1},
        actor="bi",
        source=FILE,
        note="Ppto.xlsx",
        now=NOW,
    )
    assert first[0].version == 1 and {b.version for b in second} == {2, 1}
    budgets = await repository.budgets(PERIOD)
    assert budgets["101"].budget_sales == D(1000) and budgets["101"].budget_margin == D("0.18")
    with pytest.raises(ConflictError):
        await repository.save_budgets(
            PERIOD, {"101": {"budget_sales": D(1)}}, {"101": 1}, actor="bi", source=MANUAL,
            note=None, now=NOW,
        )  # fmt: skip
    history = await repository.changes(PERIOD, "101", 10)
    assert [item.source for item in history] == [FILE, MANUAL]

    month = await repository.period(PERIOD)
    assignments = {"c1": Assignment("c1", "101", "Ana")}
    rows = rules.build_rows(
        PERIOD,
        sales=await repository.sales([PERIOD, BEFORE]),
        assignments=assignments,
        reps=await repository.reps(),
        budgets=budgets,
    )
    picture = Picture(rows, [(assignments["c1"], sold("c1", "12", "9", 3))])
    with pytest.raises(ConflictError):
        await repository.close(PERIOD, picture, month.version - 1, "lider", NOW)
    closed = await repository.close(PERIOD, picture, month.version, "lider", NOW)
    assert closed.status == CLOSED
    frozen = {row.code: row for row in await repository.snapshot(PERIOD)}
    assert frozen["101"].current.net == D(12) and frozen["101"].average.net == D(7)
    assert frozen["101"].budget.budget_margin == D("0.18")
    with pytest.raises(ConflictError):
        await repository.save_budgets(
            PERIOD, {"101": {"budget_sales": D(1)}}, {}, actor="bi", source=MANUAL, note=None,
            now=NOW,
        )  # fmt: skip

    reopened = await repository.reopen(PERIOD, "lider", NOW)
    assert reopened.status == OPEN and await repository.snapshot(PERIOD) == []
