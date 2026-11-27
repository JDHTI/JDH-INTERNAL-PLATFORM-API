"""The commercial budget's tables in PostgreSQL."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.infrastructure.database import Database
from app.modules.commercial_budget.domain.entities import (
    BUDGET_FIELDS,
    CLOSED,
    NO_REP,
    OPEN,
    Budget,
    BudgetChange,
    BudgetPeriod,
    CustomerMonth,
    Figures,
    MonthLoad,
    RepMonth,
    RepRow,
    SalesRep,
    Snapshot,
)
from app.modules.commercial_budget.domain.rules import sort_rows
from app.modules.commercial_budget.infrastructure.models import (
    SalesBudgetChangeModel,
    SalesBudgetModel,
    SalesBudgetPeriodModel,
    SalesBudgetSnapshotCustomerModel,
    SalesBudgetSnapshotRepModel,
    SalesMonthCustomerModel,
    SalesMonthLoadModel,
    SalesRepModel,
)

P = SalesBudgetPeriodModel
B = SalesBudgetModel
ZERO = Decimal(0)
_CHUNK = 2000


def _period(model: SalesBudgetPeriodModel | None, period: str) -> BudgetPeriod:
    if model is None:
        return BudgetPeriod(period)
    return BudgetPeriod(
        period=model.period,
        status=model.status,
        closed_at=model.closed_at,
        closed_by=model.closed_by,
        reopened_at=model.reopened_at,
        reopened_by=model.reopened_by,
        version=model.version,
    )


def _budget(model: SalesBudgetModel) -> Budget:
    return Budget(
        period=model.period,
        salesperson_code=model.salesperson_code,
        budget_sales=model.budget_sales,
        budget_margin=model.budget_margin,
        budget_customers=model.budget_customers,
        budget_collection=model.budget_collection,
        version=model.version,
        updated_at=model.updated_at,
        updated_by=model.updated_by,
    )


def _json(value: Any) -> Any:
    return None if value is None else str(value)


def _load(model: SalesMonthLoadModel) -> MonthLoad:
    return MonthLoad(model.period, model.loaded_at, model.customers, model.lines, model.net)


async def _insert(session: AsyncSession, table: Any, rows: list[dict]) -> None:
    for start in range(0, len(rows), _CHUNK):
        await session.execute(insert(table), rows[start : start + _CHUNK])


# Each month's sales per current rep (``customers.salesperson_code``); the sales of a customer
# with no rep, or not in the customer master, go to the blank code (shown as «sin vendedor»).
_REP_MONTHS = text(
    """
    select s.period, nullif(btrim(c.salesperson_code), '') as code,
           max(nullif(btrim(c.salesperson_name), '')) as name,
           sum(s.net) as net, sum(s.cost) as cost,
           count(*) filter (where s.net > 0) as customers
    from sales_month_customers s
    left join customers c on c.cliente_key = s.customer_key
    where s.period = any(:months)
    group by 1, 2
    """
)
_SNAPSHOT_CUSTOMERS = text(
    """
    insert into sales_budget_snapshot_customers
        (period, customer_key, salesperson_code, salesperson_name, net, cost, lines)
    select s.period, s.customer_key, nullif(btrim(c.salesperson_code), ''),
           nullif(btrim(c.salesperson_name), ''), s.net, s.cost, s.lines
    from sales_month_customers s
    left join customers c on c.cliente_key = s.customer_key
    where s.period = :period
    """
)


async def _lock(session: AsyncSession, name: str) -> None:
    """A lock held until the transaction ends, so two copies of the same data never mix."""

    await session.execute(
        text("select pg_advisory_xact_lock(hashtext(:name))"), {"name": f"commercial_budget:{name}"}
    )


async def _locked_period(session: AsyncSession, period: str) -> SalesBudgetPeriodModel:
    """The month's row, created when missing, locked for the transaction."""

    await session.execute(
        pg_insert(P)
        .values(period=period, status=OPEN)
        .on_conflict_do_nothing(index_elements=[P.period])
    )
    return (
        await session.execute(select(P).where(P.period == period).with_for_update())
    ).scalar_one()


class SqlAlchemyBudgetRepository:
    """Sales copied by month, the rep master, budgets and closed months."""

    def __init__(self, database: Database) -> None:
        self.database = database

    # --- sales copied from the warehouse ---

    async def replace_month(
        self, period: str, sales: Sequence[CustomerMonth], now: datetime
    ) -> MonthLoad:
        """Store a month's sales in place of the previous copy, in one transaction."""

        rows = [
            {
                "period": period,
                "customer_key": item.customer_key,
                "net": item.net,
                "cost": item.cost,
                "lines": item.lines,
            }
            for item in sales
        ]
        load = {
            "period": period,
            "loaded_at": now,
            "customers": len(rows),
            "lines": sum(item.lines for item in sales),
            "net": sum((item.net for item in sales), ZERO),
        }
        async with self.database.session_factory() as session, session.begin():
            # Two copies of the same month at once (the job and a person, 2026-11-26) both
            # deleted and both inserted: a duplicate key. One waits for the other.
            await _lock(session, f"sales_month:{period}")
            await session.execute(
                delete(SalesMonthCustomerModel).where(SalesMonthCustomerModel.period == period)
            )
            await _insert(session, SalesMonthCustomerModel, rows)
            statement = pg_insert(SalesMonthLoadModel).values(**load)
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[SalesMonthLoadModel.period],
                    set_={key: statement.excluded[key] for key in load if key != "period"},
                )
            )
        return MonthLoad(**load)

    async def loads(self) -> dict[str, MonthLoad]:
        async with self.database.session_factory() as session:
            models = (await session.execute(select(SalesMonthLoadModel))).scalars().all()
        return {model.period: _load(model) for model in models}

    async def rep_months(self, months: Sequence[str]) -> list[RepMonth]:
        """Each month's figures per current rep, grouped in the database.

        The months' sales by customer (about 10,000 per month) joined to each customer's rep
        (``customers``, 73,500 on 2026-11-26) come back as a few hundred rows: carrying them
        to the API took about 2.5 s per board on the event loop; grouped here, 150 ms.
        """

        if not months:
            return []
        async with self.database.session_factory() as session:
            rows = (await session.execute(_REP_MONTHS, {"months": list(months)})).all()
        return [
            RepMonth(
                period=row.period,
                code=row.code or NO_REP,
                name=row.name,
                net=row.net or ZERO,
                cost=row.cost or ZERO,
                customers=int(row.customers or 0),
            )
            for row in rows
        ]

    # --- reps ---

    async def replace_reps(self, reps: Sequence[SalesRep], now: datetime) -> int:
        """Store the rep master in place of the previous copy (an empty read keeps it)."""

        if not reps:
            return 0
        unique = {rep.code: rep for rep in reps}
        rows = [
            {
                "code": rep.code,
                "name": rep.name,
                "team": rep.team,
                "team_class": rep.team_class,
                "regional": rep.regional,
                "business_unit": rep.business_unit,
                "is_active": rep.is_active,
                "synced_at": now,
            }
            for rep in unique.values()
        ]
        async with self.database.session_factory() as session, session.begin():
            await _lock(session, "sales_reps")
            await session.execute(delete(SalesRepModel))
            await _insert(session, SalesRepModel, rows)
        return len(rows)

    async def reps(self) -> dict[str, SalesRep]:
        async with self.database.session_factory() as session:
            models = (await session.execute(select(SalesRepModel))).scalars().all()
        return {
            model.code: SalesRep(
                code=model.code,
                name=model.name,
                team=model.team,
                team_class=model.team_class,
                regional=model.regional,
                business_unit=model.business_unit,
                is_active=model.is_active,
            )
            for model in models
        }

    # --- months and budgets ---

    async def period(self, period: str) -> BudgetPeriod:
        async with self.database.session_factory() as session:
            model = (
                await session.execute(select(P).where(P.period == period))
            ).scalar_one_or_none()
        return _period(model, period)

    async def periods(self) -> list[BudgetPeriod]:
        async with self.database.session_factory() as session:
            models = (await session.execute(select(P))).scalars().all()
        return [_period(model, model.period) for model in models]

    async def budgets(self, period: str) -> dict[str, Budget]:
        async with self.database.session_factory() as session:
            models = (await session.execute(select(B).where(B.period == period))).scalars().all()
        return {model.salesperson_code: _budget(model) for model in models}

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
        """Write the fields given of each rep's budget and their history, in one transaction.

        Raises:
            ConflictError: The month is closed, or a budget's version is not the one sent.
        """

        async with self.database.session_factory() as session, session.begin():
            month = await _locked_period(session, period)
            if month.status == CLOSED:
                raise ConflictError(f"El presupuesto de {period} está cerrado")
            existing = {
                model.salesperson_code: model
                for model in (
                    await session.execute(
                        select(B)
                        .where(B.period == period, B.salesperson_code.in_(list(changes)))
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            }
            saved: list[SalesBudgetModel] = []
            history: list[dict] = []
            for code, values in changes.items():
                model = existing.get(code)
                expected = versions.get(code)
                if expected is not None and (model.version if model else 0) != expected:
                    raise ConflictError(
                        f"El presupuesto del vendedor {code} cambió mientras lo editabas; "
                        "recarga e intenta de nuevo"
                    )
                if model is None:
                    model = SalesBudgetModel(
                        id=uuid4(),
                        period=period,
                        salesperson_code=code,
                        version=0,
                        updated_at=now,
                        updated_by=actor,
                    )
                    session.add(model)
                before = {name: _json(getattr(model, name, None)) for name in values}
                for name in BUDGET_FIELDS:
                    if name in values:
                        setattr(model, name, values[name])
                after = {name: _json(values[name]) for name in values}
                if before == after and model.version:
                    saved.append(model)
                    continue
                model.version = (model.version or 0) + 1
                model.updated_at = now
                model.updated_by = actor
                saved.append(model)
                history.append(
                    {
                        "id": uuid4(),
                        "period": period,
                        "salesperson_code": code,
                        "before": before,
                        "after": after,
                        "source": source,
                        "note": note,
                        "actor": actor,
                        "at": now,
                    }
                )
            await session.flush()
            await _insert(session, SalesBudgetChangeModel, history)
            if history:
                month.version += 1
            return [_budget(model) for model in saved]

    async def changes(
        self, period: str, salesperson_code: str | None, limit: int
    ) -> list[BudgetChange]:
        C = SalesBudgetChangeModel
        query = select(C).where(C.period == period)
        if salesperson_code:
            query = query.where(C.salesperson_code == salesperson_code)
        async with self.database.session_factory() as session:
            models = (
                (await session.execute(query.order_by(C.at.desc()).limit(limit))).scalars().all()
            )
        return [
            BudgetChange(
                period=model.period,
                salesperson_code=model.salesperson_code,
                before=model.before,
                after=model.after,
                source=model.source,
                note=model.note,
                actor=model.actor,
                at=model.at,
            )
            for model in models
        ]

    # --- close ---

    async def close(
        self,
        period: str,
        snapshot: Snapshot,
        expected_version: int,
        actor: str,
        now: datetime,
    ) -> BudgetPeriod:
        """Store the month's picture and close it, in one transaction.

        Raises:
            ConflictError: It is closed already, or the budget changed after the picture.
        """

        async with self.database.session_factory() as session, session.begin():
            month = await _locked_period(session, period)
            if month.status == CLOSED:
                raise ConflictError(f"El presupuesto de {period} ya está cerrado")
            if month.version != expected_version:
                raise ConflictError(
                    "El presupuesto cambió mientras se cerraba el mes; intenta de nuevo"
                )
            await _drop_snapshot(session, period)
            await _insert(
                session,
                SalesBudgetSnapshotRepModel,
                [_snapshot_row(period, row) for row in snapshot.rows],
            )
            # Who had which customer that month, copied in the database (thousands of rows).
            await session.execute(_SNAPSHOT_CUSTOMERS, {"period": period})
            month.status = CLOSED
            month.closed_at = now
            month.closed_by = actor
            month.version += 1
            await session.flush()
            return _period(month, period)

    async def reopen(self, period: str, actor: str, now: datetime) -> BudgetPeriod:
        """Open a closed month again and drop its picture.

        Raises:
            NotFoundError: The month was never closed.
            ConflictError: It is not closed.
        """

        async with self.database.session_factory() as session, session.begin():
            month = (
                await session.execute(select(P).where(P.period == period).with_for_update())
            ).scalar_one_or_none()
            if month is None:
                raise NotFoundError(f"El presupuesto de {period} no se ha cerrado")
            if month.status != CLOSED:
                raise ConflictError(f"El presupuesto de {period} no está cerrado")
            await _drop_snapshot(session, period)
            month.status = OPEN
            month.reopened_at = now
            month.reopened_by = actor
            month.version += 1
            await session.flush()
            return _period(month, period)

    async def snapshot(self, period: str) -> list[RepRow]:
        S = SalesBudgetSnapshotRepModel
        async with self.database.session_factory() as session:
            models = (await session.execute(select(S).where(S.period == period))).scalars().all()
        rows = []
        for model in models:
            budget = Budget(
                period=period,
                salesperson_code=model.code,
                budget_sales=model.budget_sales,
                budget_margin=model.budget_margin,
                budget_customers=model.budget_customers,
                budget_collection=model.budget_collection,
            )
            rows.append(
                RepRow(
                    code=model.code,
                    name=model.name,
                    regional=model.regional,
                    business_unit=model.business_unit,
                    team=model.team,
                    team_class=model.team_class,
                    is_active=model.is_active,
                    current=Figures(
                        model.current_net, model.current_margin, model.current_customers
                    ),
                    average=Figures(
                        model.average_net,
                        model.average_margin,
                        model.average_customers,
                        model.average_months,
                    ),
                    last_year=Figures(
                        model.last_year_net, model.last_year_margin, model.last_year_customers
                    ),
                    budget=None if budget.is_empty else budget,
                )
            )
        return sort_rows(rows)

    async def snapshot_customers(self, period: str) -> int:
        """How many customer branches a closed month froze."""

        S = SalesBudgetSnapshotCustomerModel
        async with self.database.session_factory() as session:
            return int(
                (await session.execute(select(func.count()).where(S.period == period))).scalar_one()
            )


async def _drop_snapshot(session: AsyncSession, period: str) -> None:
    await session.execute(
        delete(SalesBudgetSnapshotRepModel).where(SalesBudgetSnapshotRepModel.period == period)
    )
    await session.execute(
        delete(SalesBudgetSnapshotCustomerModel).where(
            SalesBudgetSnapshotCustomerModel.period == period
        )
    )


def _snapshot_row(period: str, row: RepRow) -> dict[str, Any]:
    budget = row.budget
    return {
        "period": period,
        "code": row.code,
        "name": row.name,
        "regional": row.regional,
        "business_unit": row.business_unit,
        "team": row.team,
        "team_class": row.team_class,
        "is_active": row.is_active,
        "current_net": row.current.net,
        "current_margin": row.current.margin,
        "current_customers": row.current.customers,
        "average_net": row.average.net,
        "average_margin": row.average.margin,
        "average_customers": row.average.customers,
        "average_months": row.average.months,
        "last_year_net": row.last_year.net,
        "last_year_margin": row.last_year.margin,
        "last_year_customers": row.last_year.customers,
        **{name: getattr(budget, name) if budget else None for name in BUDGET_FIELDS},
    }


__all__ = ["SqlAlchemyBudgetRepository"]
