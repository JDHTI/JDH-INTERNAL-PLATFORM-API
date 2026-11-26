"""Opt-in: the budget repository on PostgreSQL, migrated to head (0093).

Runs with ``COMMERCIAL_BUDGET_TEST_DATABASE_URL`` (never the shared database: it replaces
``sales_reps``).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.core.errors import ConflictError
from app.infrastructure.database import Database
from app.modules.commercial_budget.domain import rules
from app.modules.commercial_budget.domain.entities import CLOSED, FILE, MANUAL, NO_REP, OPEN
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
from app.modules.commercial_masters.infrastructure.models import CustomerModel
from tests.modules.commercial_budget.fakes import rep, sold

pytestmark = pytest.mark.skipif(
    not os.getenv("COMMERCIAL_BUDGET_TEST_DATABASE_URL"),
    reason="Dedicated PostgreSQL integration URL not set",
)

# Months no real budget uses.
PERIOD, BEFORE = "2001-03", "2001-02"
NOW = datetime(2001, 4, 2, tzinfo=UTC)
D = Decimal
# Customer branches only this test uses (deleted before and after).
C1, C2, NOBODY = "bit-c1", "bit-c2", "bit-nobody"


@pytest.fixture
async def repository():
    database = Database(os.environ["COMMERCIAL_BUDGET_TEST_DATABASE_URL"])
    customers = [
        (C1, "101", "Ana"),
        (C2, "102", "Luis"),
        (NOBODY, None, None),
    ]
    async with database.session_factory() as session, session.begin():
        await session.execute(delete(CustomerModel).where(CustomerModel.cliente_key.like("bit-%")))
        session.add_all(
            CustomerModel(
                id=uuid4(),
                cliente_key=key,
                company_id=1,
                nit=key,
                salesperson_code=code,
                salesperson_name=name,
                is_active=True,
                synced_at=NOW,
            )
            for key, code, name in customers
        )
    try:
        yield SqlAlchemyBudgetRepository(database)
    finally:
        async with database.session_factory() as session, session.begin():
            await session.execute(
                delete(CustomerModel).where(CustomerModel.cliente_key.like("bit-%"))
            )
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
    await repository.replace_month(PERIOD, [sold(C1, "10", "8"), sold(C2, "5", "4")], NOW)
    load = await repository.replace_month(
        PERIOD, [sold(C1, "12", "9", 3), sold(C2, "-2", "-1"), sold(NOBODY, "4", "3")], NOW
    )
    await repository.replace_month(BEFORE, [sold(C1, "7", "6")], NOW)
    assert (load.customers, load.lines, load.net) == (3, 5, D(14))
    assert {PERIOD, BEFORE} <= set(await repository.loads())
    # Grouped in the database by each customer's current rep; no rep goes to «sin vendedor».
    months = {(item.period, item.code): item for item in await repository.rep_months([PERIOD])}
    assert months[(PERIOD, "101")].net == D(12) and months[(PERIOD, "101")].customers == 1
    assert months[(PERIOD, "102")].net == D(-2) and months[(PERIOD, "102")].customers == 0
    assert months[(PERIOD, NO_REP)].net == D(4) and months[(PERIOD, "101")].name == "Ana"

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
    rows = rules.build_rows(
        PERIOD,
        months=await repository.rep_months([PERIOD, BEFORE]),
        reps=await repository.reps(),
        budgets=budgets,
    )
    picture = Picture(rows)
    with pytest.raises(ConflictError):
        await repository.close(PERIOD, picture, month.version - 1, "lider", NOW)
    closed = await repository.close(PERIOD, picture, month.version, "lider", NOW)
    assert closed.status == CLOSED
    frozen = {row.code: row for row in await repository.snapshot(PERIOD)}
    assert frozen["101"].current.net == D(12) and frozen["101"].average.net == D(7)
    assert frozen["101"].budget.budget_margin == D("0.18")
    assert await repository.snapshot_customers(PERIOD) == 3
    with pytest.raises(ConflictError):
        await repository.save_budgets(
            PERIOD, {"101": {"budget_sales": D(1)}}, {}, actor="bi", source=MANUAL, note=None,
            now=NOW,
        )  # fmt: skip

    reopened = await repository.reopen(PERIOD, "lider", NOW)
    assert reopened.status == OPEN and await repository.snapshot(PERIOD) == []
