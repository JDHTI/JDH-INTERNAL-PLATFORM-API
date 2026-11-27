"""The budget service: the board, the budget, the workbook, close, reopen and the copy."""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from openpyxl import Workbook, load_workbook

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError
from app.modules.commercial_budget.application.service import BudgetService, Filters
from app.modules.commercial_budget.domain.entities import CLOSED, NO_REP, OPEN
from app.modules.commercial_budget.infrastructure.budget_file import (
    budget_template,
    read_budget_file,
)
from app.modules.commercial_budget.infrastructure.export_file import board_workbook
from app.modules.jobs.domain.entities import JobRun
from app.modules.jobs.domain.registry import RunStatus, Trigger
from tests.modules.commercial_budget.fakes import (
    FakeRepository,
    FakeWarehouse,
    Policy,
    Scopes,
    rep,
    sold,
)
from tests.modules.fakes import principal

D = Decimal
TODAY = date(2026, 10, 5)
ANALYST = principal("analista.bi@jdh.com.co")
COORDINATOR = principal("coordinador@jdh.com.co")
READER = principal("lector@jdh.com.co")
LEADER = principal("lider.comercial@jdh.com.co")
OUTSIDER = principal("otro@jdh.com.co")
REPS = [
    rep("101", "Arismendy Betancur Viviana Maria"),
    rep("102", "Giraldo Lopez Carlos Alberto"),
    rep("201", "Peralta Valderrama Paula Andrea", team="Zona 1 Bogota", regional="CENTRO"),
]
MONTHS = {
    "2026-09": [sold("c1", "1000", "800"), sold("c3", "500", "400"), sold("c9", "10", "5")],
    "2026-08": [sold("c1", "900", "720")],
    "2025-09": [sold("c3", "400", "300")],
}


@pytest.fixture
def repository() -> FakeRepository:
    found = FakeRepository()
    found.assign("c1", "101", "Arismendy")
    found.assign("c3", "201", "Peralta")
    return found


@pytest.fixture
def warehouse() -> FakeWarehouse:
    return FakeWarehouse(MONTHS, REPS)


@pytest.fixture
def service(repository, warehouse) -> BudgetService:
    return BudgetService(
        repository,
        warehouse,
        Policy(managers={ANALYST.email, COORDINATOR.email}, readers={READER.email}),
        Policy(managers={LEADER.email}),
        Scopes({COORDINATOR.email: ("101", "102")}),
        start_period="2026-01",
        backfill_months=20,
        writer=board_workbook,
        template_writer=budget_template,
        reader=read_budget_file,
        today=lambda: TODAY,
    )


@pytest.fixture
async def loaded(service) -> BudgetService:
    await service.refresh()
    return service


async def test_the_copy_takes_the_reps_the_open_months_and_the_missing_history(
    service, repository, warehouse
) -> None:
    report = await service.refresh()

    assert report["vendedores"] == 3
    assert warehouse.reads[:2] == ["2026-10", "2026-09"]
    # From a year before the start (2025-01) to today: 22 months, newest first.
    assert len(warehouse.reads) == 22 and report["pendientes"] == []
    assert (await service.refresh())["meses"].keys() == {"2026-10", "2026-09"}


class Runs:
    def __init__(self) -> None:
        self.items: list[JobRun] = []

    async def queue(self, job, trigger, now, *, triggered_by=None) -> JobRun:
        if any(item.status is RunStatus.QUEUED for item in self.items):
            raise ConflictError("en cola")
        run = JobRun(uuid4(), job, trigger, RunStatus.QUEUED, now, triggered_by=triggered_by)
        self.items.append(run)
        return run

    async def latest(self, job) -> JobRun | None:
        return self.items[-1] if self.items else None


async def test_a_manual_sync_is_queued_for_the_worker_and_answers_at_once(
    repository, warehouse
) -> None:
    runs, handed = Runs(), []
    service = BudgetService(
        repository,
        warehouse,
        Policy(managers={ANALYST.email}, readers={READER.email}),
        Policy(),
        Scopes(),
        runs=runs,
        jobs_enabled=True,
        request_run=handed.append,
        today=lambda: TODAY,
    )

    run = await service.sync(ANALYST)

    assert run.job == "commercial-budget-sales" and run.trigger is Trigger.MANUAL
    assert handed == [run.id] and warehouse.reads == []
    assert (await service.latest_sync(READER)) == run
    with pytest.raises(ConflictError, match="en cola o en curso"):
        await service.sync(ANALYST)
    with pytest.raises(ForbiddenError):
        await service.sync(READER)


async def test_a_sync_without_the_worker_is_refused(service) -> None:
    with pytest.raises(ConflictError, match="JOBS_ENABLED"):
        await service.sync(ANALYST)


async def test_permissions(service) -> None:
    analyst = await service.permissions(ANALYST)
    leader = await service.permissions(LEADER)
    outsider = await service.permissions(OUTSIDER)

    assert (analyst.can_read, analyst.can_manage, analyst.can_close) == (True, True, False)
    assert (await service.permissions(COORDINATOR)).scope == "team"
    assert not leader.can_read and not leader.can_close
    assert not outsider.can_read and outsider.scope == "none"


async def test_months_go_from_the_start_to_next_month(service) -> None:
    found = await service.periods(READER)

    assert found[0].period.period == "2026-11" and found[-1].period.period == "2026-01"
    assert [item.period.period for item in found if item.is_current] == ["2026-10"]
    with pytest.raises(InvalidInputError):
        await service.board(READER, "2026-12")
    with pytest.raises(InvalidInputError):
        await service.board(READER, "2025-12")


async def test_the_board_has_the_three_cuts_totals_and_the_unassigned(loaded) -> None:
    view = await loaded.board(ANALYST, "2026-09")
    rows = {row.code: row for row in view.rows}

    assert rows["101"].current.net == D(1000) and rows["101"].average.net == D(900)
    assert rows["201"].last_year.net == D(400)
    assert rows[NO_REP].current.net == D(10)
    assert view.totals.current.net == D(1510)
    assert view.previous == ("2026-06", "2026-07", "2026-08") and view.last_year == "2025-09"
    assert view.missing_months == [] and view.sales_loaded_at is not None
    assert view.options["regionals"] == ["ANTIOQUIA", "CENTRO"]
    filtered = await loaded.board(ANALYST, "2026-09", Filters(regionals=["CENTRO"]))
    assert [row.code for row in filtered.rows] == ["201"]


async def test_a_coordinator_sees_and_fills_only_their_team(loaded) -> None:
    view = await loaded.board(COORDINATOR, "2026-09")

    assert {row.code for row in view.rows} == {"101", "102"}
    with pytest.raises(ForbiddenError, match="alcance"):
        await loaded.update_budgets(
            COORDINATOR, "2026-09", [{"salesperson_code": "201", "budget_sales": 1}]
        )


async def test_budget_fields_are_filled_one_by_one(loaded, repository) -> None:
    await loaded.update_budgets(
        ANALYST, "2026-09", [{"salesperson_code": "101", "budget_sales": "1200"}]
    )
    saved = await loaded.update_budgets(
        ANALYST,
        "2026-09",
        [{"salesperson_code": "101", "budget_margin": "0.18", "version": 1}],
        note="Comité",
    )

    assert saved[0].budget_sales == D(1200) and saved[0].budget_margin == D("0.18")
    assert saved[0].budget_customers is None and saved[0].version == 2
    view = await loaded.board(ANALYST, "2026-09")
    row = next(row for row in view.rows if row.code == "101")
    assert row.budget.budget_sales == D(1200)
    history = await loaded.changes(ANALYST, "2026-09", "101")
    assert [item.after for item in history] == [{"budget_margin": "0.18"}, {"budget_sales": "1200"}]
    with pytest.raises(ConflictError):
        await loaded.update_budgets(
            ANALYST, "2026-09", [{"salesperson_code": "101", "budget_sales": 1, "version": 1}]
        )


@pytest.mark.parametrize(
    "item, message",
    [
        ({"salesperson_code": "999", "budget_sales": 1}, "maestro"),
        ({"salesperson_code": "101", "budget_margin": "18"}, "fracción"),
        ({"salesperson_code": "101", "budget_customers": "1.5"}, "enteros"),
        ({"salesperson_code": "101", "budget_sales": "-1"}, "negativo"),
        ({"salesperson_code": "101"}, "No hay cambios"),
    ],
)
async def test_budget_values_are_checked(loaded, item, message) -> None:
    with pytest.raises(InvalidInputError, match=message):
        await loaded.update_budgets(ANALYST, "2026-09", [item])


async def test_a_reader_does_not_fill(loaded) -> None:
    with pytest.raises(ForbiddenError):
        await loaded.update_budgets(
            READER, "2026-09", [{"salesperson_code": "101", "budget_sales": 1}]
        )


def _workbook(rows: list[list]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


async def test_bis_flat_file_loads_by_name_and_blank_cells_wait(loaded) -> None:
    content = _workbook(
        [
            ["REGIONAL", "UNIDAD NEGOCIO", "EQUIPO", "NOMBRE VENDEDOR CLIENTE", "PPTO VENTA",
             "PPTO MARGEN", "PPTO # CLIENTES", "PPTO RECAUDO", "FECHA"],
            ["Antioquia", "Food Service", "Senior Medellin", "Arismendy Betancur Viviana Maria",
             680000000, 0.16, 120, 730000000, None],
            ["Antioquia", "Food Service", "Senior Medellin", "Giraldo López Carlos Alberto",
             520000000, None, None, None, None],
            ["Antioquia", "Food Service", "Senior Medellin", "Nadie Conocido", 1, 0.1, 1, 1, None],
        ]
    )  # fmt: skip

    simulated = await loaded.upload_budgets(
        ANALYST, "2026-09", "Ppto Septiembre 2026.xlsx", content, apply=False
    )
    assert (simulated.rows, simulated.matched, simulated.changed) == (3, 2, 2)
    assert not simulated.applied and len(simulated.warnings) == 2
    assert simulated.problems[0].value == "Nadie Conocido"
    assert (await loaded.repository.budgets("2026-09")) == {}

    applied = await loaded.upload_budgets(
        ANALYST, "2026-09", "Ppto Septiembre 2026.xlsx", content, apply=True
    )
    budgets = await loaded.repository.budgets("2026-09")
    assert applied.applied and budgets["101"].budget_margin == D("0.16")
    assert budgets["102"].budget_sales == D(520000000) and budgets["102"].budget_margin is None
    again = await loaded.upload_budgets(ANALYST, "2026-09", "x.xlsx", content, apply=True)
    assert again.changed == 0 and again.unchanged == 2


async def test_the_template_goes_back_in_with_its_codes(loaded) -> None:
    await loaded.update_budgets(
        ANALYST, "2026-09", [{"salesperson_code": "201", "budget_sales": 700}]
    )
    template = await loaded.budget_template(ANALYST, "2026-09")
    workbook = load_workbook(io.BytesIO(template.content))
    sheet = workbook["Presupuesto"]
    for row in sheet.iter_rows(min_row=2):
        if row[0].value == "101":
            row[6].value = "18%"

    output = io.BytesIO()
    workbook.save(output)
    report = await loaded.upload_budgets(
        ANALYST, "2026-09", "plantilla.xlsx", output.getvalue(), apply=True
    )

    budgets = await loaded.repository.budgets("2026-09")
    assert report.problems == [] and report.changed == 1
    assert budgets["101"].budget_margin == D("0.18") and budgets["201"].budget_sales == D(700)


async def test_only_a_leader_closes_a_finished_month_and_it_freezes(loaded, repository) -> None:
    with pytest.raises(ForbiddenError):
        await loaded.close(ANALYST, "2026-09")
    with pytest.raises(InvalidInputError, match="terminado"):
        await loaded.close(LEADER, "2026-10")

    closed = await loaded.close(LEADER, "2026-09")
    # The customer moves to another rep after the close: the closed month does not change.
    repository.assign("c1", "102", "Giraldo")
    view = await loaded.board(ANALYST, "2026-09")

    assert closed.status == CLOSED and closed.closed_by == LEADER.email
    assert next(row for row in view.rows if row.code == "101").current.net == D(1000)
    assert repository.snapshot_customers["2026-09"]["c1"].salesperson_code == "101"
    assert len(repository.snapshot_customers["2026-09"]) == 3
    with pytest.raises(ConflictError, match="cerrado"):
        await loaded.update_budgets(
            ANALYST, "2026-09", [{"salesperson_code": "101", "budget_sales": 1}]
        )
    with pytest.raises(ConflictError):
        await loaded.close(LEADER, "2026-09")

    reopened = await loaded.reopen(LEADER, "2026-09")
    live = await loaded.board(ANALYST, "2026-09")
    assert reopened.status == OPEN
    assert next(row for row in live.rows if row.code == "102").current.net == D(1000)


async def test_a_month_without_its_sales_does_not_close(service) -> None:
    with pytest.raises(InvalidInputError, match="sincroniza"):
        await service.close(LEADER, "2026-09")


async def test_the_export_follows_bis_template(loaded) -> None:
    await loaded.update_budgets(
        ANALYST,
        "2026-09",
        [{"salesperson_code": "101", "budget_sales": 1200, "budget_margin": "0.2"}],
    )

    result = await loaded.export(ANALYST, "2026-09")
    sheet = load_workbook(io.BytesIO(result.content))["Presupuesto"]
    names = [row[4] for row in sheet.iter_rows(min_row=4, values_only=True)]

    assert result.filename == "presupuesto-comercial-2026-09.xlsx"
    assert "TOTAL SENIOR MEDELLIN" in names and "TOTAL ANTIOQUIA" in names and names[-1] == "TOTAL"
