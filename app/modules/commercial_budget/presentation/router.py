"""Endpoints of the commercial budget (``/commercial-budget``)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, File, Form, Path, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.commercial_budget.application.service import (
    BoardView,
    BudgetService,
    Filters,
    UploadReport,
)
from app.modules.commercial_budget.domain import rules
from app.modules.commercial_budget.domain.entities import (
    BUDGET_FIELD_LABELS,
    STATUS_LABELS,
    Budget,
    BudgetPeriod,
    Figures,
    RepRow,
    Totals,
)
from app.modules.commercial_budget.domain.modules import BUDGET
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.jobs.domain.entities import JobRun

router = APIRouter()
_GATE = Depends(require_permission(BUDGET))
_service = Depends(Provide[ApplicationContainer.v1_container.commercial_budget_service])
_principal = Depends(get_current_principal)
_PERIOD = Path(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="Month, YYYY-MM")
_FILE = File(...)
_NOTE = Form(default=None, max_length=500)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class PermissionsResponse(BaseModel):
    can_read: bool
    # Fill the budget (by hand or with the workbook).
    can_manage: bool
    # Close and reopen a month: the leaders of BI and Comercial and administrators.
    can_close: bool
    scope: Literal["all", "team", "mine", "none"]


class PeriodResponse(BaseModel):
    period: str
    status: Literal["abierto", "cerrado"]
    status_label: str
    is_current: bool
    is_finished: bool
    closed_at: datetime | None
    closed_by: str | None
    reopened_at: datetime | None
    reopened_by: str | None


class FiguresResponse(BaseModel):
    """A cut: sale (net before VAT), margin not recomposed (fraction) and customers."""

    net: Decimal
    margin: Decimal | None
    customers: Decimal
    # Months with sales behind the average (the average cut only).
    months: int | None = None


class BudgetResponse(BaseModel):
    budget_sales: Decimal | None
    budget_margin: Decimal | None
    budget_customers: int | None
    budget_collection: Decimal | None
    version: int
    updated_at: datetime | None
    updated_by: str | None


class DerivedResponse(BaseModel):
    """Budget against the real: ``variation`` = budget / last year − 1, ``compliance`` = sale /
    budget, ``margin_gap`` = margin − budget margin, ``customers_gap`` = customers − budget."""

    variation: Decimal | None
    compliance: Decimal | None
    margin_gap: Decimal | None
    customers_gap: Decimal | None


class RowResponse(BaseModel):
    salesperson_code: str
    name: str | None
    regional: str | None
    business_unit: str | None
    team: str | None
    team_class: str | None
    is_active: bool
    current: FiguresResponse
    average: FiguresResponse
    last_year: FiguresResponse
    budget: BudgetResponse | None
    derived: DerivedResponse


class TotalsResponse(BaseModel):
    rows: int
    current: FiguresResponse
    average: FiguresResponse
    last_year: FiguresResponse
    budget_sales: Decimal | None
    budget_margin: Decimal | None
    budget_customers: int | None
    budget_collection: Decimal | None
    derived: DerivedResponse


class GroupResponse(TotalsResponse):
    key: str | None


class TeamGroupResponse(TotalsResponse):
    """A team's subtotal under one regional (a team under two regionals gives two)."""

    regional: str | None
    team: str | None


class SalespersonOption(BaseModel):
    code: str
    name: str | None
    team: str | None


class OptionsResponse(BaseModel):
    regionals: list[str]
    business_units: list[str]
    teams: list[str]
    salespeople: list[SalespersonOption]


class WindowResponse(BaseModel):
    current: str
    previous: list[str]
    last_year: str


class BoardResponse(BaseModel):
    period: PeriodResponse
    window: WindowResponse
    totals: TotalsResponse
    rows: list[RowResponse]
    # Totals per regional, business unit and team (the team across regionals).
    groups: dict[str, list[GroupResponse]]
    # Subtotals per regional and team, for the regional → team → rep table.
    team_groups: list[TeamGroupResponse]
    options: OptionsResponse
    # Months of the window whose sales are not copied yet (an open month).
    missing_months: list[str]
    sales_loaded_at: datetime | None


class BudgetItem(BaseModel):
    """A rep's budget; only the fields sent change, ``null`` clears one."""

    model_config = ConfigDict(extra="forbid")

    salesperson_code: str = Field(min_length=1, max_length=40)
    # The version read; a concurrent change answers 409.
    version: int | None = Field(default=None, ge=0)
    budget_sales: Decimal | None = None
    budget_margin: Decimal | None = Field(default=None, description="Fraction: 0.18 = 18 %")
    budget_customers: int | None = None
    budget_collection: Decimal | None = None


class BudgetUpdate(BaseModel):
    items: list[BudgetItem] = Field(min_length=1, max_length=1000)
    note: str | None = Field(default=None, max_length=500)


class SavedBudgetResponse(BudgetResponse):
    salesperson_code: str


class ChangeResponse(BaseModel):
    salesperson_code: str
    before: dict[str, Any]
    after: dict[str, Any]
    fields: list[str]
    source: Literal["manual", "archivo"]
    note: str | None
    actor: str
    at: datetime


class ProblemResponse(BaseModel):
    row: int
    value: str | None
    reason: str


class UploadResponse(BaseModel):
    applied: bool
    rows: int
    matched: int
    changed: int
    unchanged: int
    problems: list[ProblemResponse]
    warnings: list[ProblemResponse]


def _period(item: BudgetPeriod, is_current: bool, is_finished: bool) -> PeriodResponse:
    return PeriodResponse(
        period=item.period,
        status=item.status,  # type: ignore[arg-type]
        status_label=STATUS_LABELS.get(item.status, item.status),
        is_current=is_current,
        is_finished=is_finished,
        closed_at=item.closed_at,
        closed_by=item.closed_by,
        reopened_at=item.reopened_at,
        reopened_by=item.reopened_by,
    )


def _figures(item: Figures) -> FiguresResponse:
    return FiguresResponse(
        net=item.net, margin=item.margin, customers=item.customers, months=item.months
    )


def _budget(item: Budget | None) -> BudgetResponse | None:
    if item is None or item.is_empty:
        return None
    return BudgetResponse(
        budget_sales=item.budget_sales,
        budget_margin=item.budget_margin,
        budget_customers=item.budget_customers,
        budget_collection=item.budget_collection,
        version=item.version,
        updated_at=item.updated_at,
        updated_by=item.updated_by,
    )


def _derived(
    current: Figures,
    last_year: Figures,
    budget_sales: Decimal | None,
    budget_margin: Decimal | None,
    budget_customers: int | None,
) -> DerivedResponse:
    return DerivedResponse(
        variation=rules.variation(budget_sales, last_year.net),
        compliance=rules.compliance(current.net, budget_sales),
        margin_gap=rules.margin_gap(current.margin, budget_margin),
        customers_gap=(
            None if budget_customers is None else current.customers - Decimal(budget_customers)
        ),
    )


def _row(row: RepRow) -> RowResponse:
    budget = row.budget
    return RowResponse(
        salesperson_code=row.code,
        name=row.name,
        regional=row.regional,
        business_unit=row.business_unit,
        team=row.team,
        team_class=row.team_class,
        is_active=row.is_active,
        current=_figures(row.current),
        average=_figures(row.average),
        last_year=_figures(row.last_year),
        budget=_budget(budget),
        derived=_derived(
            row.current,
            row.last_year,
            budget.budget_sales if budget else None,
            budget.budget_margin if budget else None,
            budget.budget_customers if budget else None,
        ),
    )


def _totals(item: Totals) -> dict[str, Any]:
    return {
        "rows": item.rows,
        "current": _figures(item.current),
        "average": _figures(item.average),
        "last_year": _figures(item.last_year),
        "budget_sales": item.budget_sales,
        "budget_margin": item.budget_margin,
        "budget_customers": item.budget_customers,
        "budget_collection": item.budget_collection,
        "derived": _derived(
            item.current,
            item.last_year,
            item.budget_sales,
            item.budget_margin,
            item.budget_customers,
        ),
    }


def _board(view: BoardView) -> BoardResponse:
    options = view.options
    return BoardResponse(
        period=_period(view.period, view.is_current, view.is_finished),
        window=WindowResponse(
            current=view.period.period, previous=list(view.previous), last_year=view.last_year
        ),
        totals=TotalsResponse(**_totals(view.totals)),
        rows=[_row(row) for row in view.rows],
        groups={
            by: [GroupResponse(key=key, **_totals(totals)) for key, totals in items]
            for by, items in view.groups.items()
        },
        team_groups=[
            TeamGroupResponse(regional=regional, team=team, **_totals(totals))
            for regional, team, totals in view.team_groups
        ],
        options=OptionsResponse(
            regionals=options["regionals"],
            business_units=options["business_units"],
            teams=options["teams"],
            salespeople=[
                SalespersonOption(code=code, name=name, team=team)
                for code, name, team in options["salespeople"]
            ],
        ),
        missing_months=view.missing_months,
        sales_loaded_at=view.sales_loaded_at,
    )


def _filters(
    regional: list[str], business_unit: list[str], team: list[str], salesperson: list[str], q
) -> Filters:
    return Filters(
        regionals=[item for item in regional if item],
        business_units=[item for item in business_unit if item],
        teams=[item for item in team if item],
        salespeople=[item for item in salesperson if item],
        query=q,
    )


def _upload(report: UploadReport) -> UploadResponse:
    return UploadResponse(
        applied=report.applied,
        rows=report.rows,
        matched=report.matched,
        changed=report.changed,
        unchanged=report.unchanged,
        problems=[ProblemResponse(**vars(item)) for item in report.problems],
        warnings=[ProblemResponse(**vars(item)) for item in report.warnings],
    )


def _xlsx(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type=XLSX,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/permissions", response_model=PermissionsResponse, summary="What the caller may do")
@inject
async def permissions(
    principal: Principal = _principal, service: BudgetService = _service
) -> PermissionsResponse:
    """Open to everyone: ``false`` when the caller has no access (no 403)."""

    result = await service.permissions(principal)
    return PermissionsResponse(
        can_read=result.can_read,
        can_manage=result.can_manage,
        can_close=result.can_close,
        scope=result.scope,  # type: ignore[arg-type]
    )


@router.get(
    "/periods",
    response_model=list[PeriodResponse],
    dependencies=[_GATE],
    summary="The months of the budget, the newest first (up to next month)",
)
@inject
async def list_periods(
    principal: Principal = _principal, service: BudgetService = _service
) -> list[PeriodResponse]:
    return [
        _period(item.period, item.is_current, item.is_finished)
        for item in await service.periods(principal)
    ]


class SyncResponse(BaseModel):
    """A copy of the rep master and the sales (background job ``commercial-budget-sales``)."""

    id: UUID
    status: Literal["queued", "running", "success", "partial", "failed"]
    # ``dwh`` (the day's sales load), ``scheduled`` (daily fallback), ``manual`` or ``startup``.
    trigger: str
    triggered_by: str | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    # Months copied in the run, and the history still missing.
    months: list[str]
    pending: list[str]


def _sync(run: JobRun | None) -> SyncResponse | None:
    if run is None:
        return None
    detail = run.detail or {}
    return SyncResponse(
        id=run.id,
        status=run.status.value,  # type: ignore[arg-type]
        trigger=run.trigger.value,
        triggered_by=run.triggered_by,
        queued_at=run.queued_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=run.error,
        months=sorted(detail.get("meses") or {}),
        pending=sorted(detail.get("pendientes") or []),
    )


@router.post(
    "/sync",
    response_model=SyncResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[_GATE],
    summary="Queue the copy of the rep master and the sales from the DWH",
)
@inject
async def sync(
    principal: Principal = _principal, service: BudgetService = _service
) -> SyncResponse:
    """Answers at once (``manage``): the background job copies the current month, the previous
    one when open and the missing months of the history; follow it with ``GET /sync``. It also
    runs by itself after the DWH's sales load. 409 when the worker is off or a copy is already
    queued or running."""

    return _sync(await service.sync(principal))  # type: ignore[return-value]


@router.get(
    "/sync",
    response_model=SyncResponse | None,
    dependencies=[_GATE],
    summary="The latest copy of the sales (to follow a sync)",
)
@inject
async def latest_sync(
    principal: Principal = _principal, service: BudgetService = _service
) -> SyncResponse | None:
    return _sync(await service.latest_sync(principal))


@router.get(
    "/{period}",
    response_model=BoardResponse,
    dependencies=[_GATE],
    summary="A month's board: reps with their cuts and budget, totals and groups",
)
@inject
async def get_board(
    period: str = _PERIOD,
    regional: list[str] = Query(default=[], max_length=50),  # noqa: B008
    business_unit: list[str] = Query(default=[], max_length=50),  # noqa: B008
    team: list[str] = Query(default=[], max_length=100),  # noqa: B008
    salesperson: list[str] = Query(default=[], max_length=300),  # noqa: B008
    q: str | None = Query(default=None, max_length=80),
    principal: Principal = _principal,
    service: BudgetService = _service,
) -> BoardResponse:
    """Each rep of the caller's scope: the month, the average of the three months before and
    the same month last year (sale, margin not recomposed, customers), and the budget.
    Totals and groups (regional, business unit, team) follow the filters."""

    view = await service.board(
        principal, period, _filters(regional, business_unit, team, salesperson, q)
    )
    return _board(view)


@router.put(
    "/{period}/budgets",
    response_model=list[SavedBudgetResponse],
    dependencies=[_GATE],
    summary="Fill reps' budget (only the fields sent)",
)
@inject
async def update_budgets(
    body: BudgetUpdate,
    period: str = _PERIOD,
    principal: Principal = _principal,
    service: BudgetService = _service,
) -> list[SavedBudgetResponse]:
    """409 when the month is closed or a budget changed since read; 403 for a rep out of the
    caller's scope."""

    items = [item.model_dump(exclude_unset=True) for item in body.items]
    saved = await service.update_budgets(principal, period, items, body.note)
    return [
        SavedBudgetResponse(salesperson_code=item.salesperson_code, **_budget_fields(item))
        for item in saved
    ]


def _budget_fields(item: Budget) -> dict[str, Any]:
    return {
        "budget_sales": item.budget_sales,
        "budget_margin": item.budget_margin,
        "budget_customers": item.budget_customers,
        "budget_collection": item.budget_collection,
        "version": item.version,
        "updated_at": item.updated_at,
        "updated_by": item.updated_by,
    }


@router.get(
    "/{period}/changes",
    response_model=list[ChangeResponse],
    dependencies=[_GATE],
    summary="History of the month's budget",
)
@inject
async def list_changes(
    period: str = _PERIOD,
    salesperson_code: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = _principal,
    service: BudgetService = _service,
) -> list[ChangeResponse]:
    found = await service.changes(principal, period, salesperson_code, limit)
    return [
        ChangeResponse(
            salesperson_code=item.salesperson_code,
            before=item.before,
            after=item.after,
            fields=[BUDGET_FIELD_LABELS.get(name, name) for name in item.after],
            source=item.source,  # type: ignore[arg-type]
            note=item.note,
            actor=item.actor,
            at=item.at,
        )
        for item in found
    ]


@router.get(
    "/{period}/budget-template",
    dependencies=[_GATE],
    summary="The month's budget workbook, with the caller's reps",
    response_class=Response,
)
@inject
async def budget_template(
    period: str = _PERIOD, principal: Principal = _principal, service: BudgetService = _service
) -> Response:
    result = await service.budget_template(principal, period)
    return _xlsx(result.content, result.filename)


@router.post(
    "/{period}/budget-upload",
    response_model=UploadResponse,
    dependencies=[_GATE],
    summary="Load the month's budget from a workbook (a simulation unless apply)",
)
@inject
async def budget_upload(
    period: str = _PERIOD,
    apply: bool = Query(default=False, description="Store it; a simulation otherwise"),
    file: UploadFile = _FILE,
    note: str | None = _NOTE,
    principal: Principal = _principal,
    service: BudgetService = _service,
) -> UploadResponse:
    """Columns by header: CÓDIGO VENDEDOR (or the rep's name), PPTO VENTA, PPTO MARGEN,
    PPTO # CLIENTES, PPTO RECAUDO. A blank cell leaves the field as it is."""

    content = await file.read(service.max_upload_bytes + 1)
    report = await service.upload_budgets(
        principal, period, file.filename, content, apply=apply, note=note
    )
    return _upload(report)


@router.post(
    "/{period}/close",
    response_model=PeriodResponse,
    dependencies=[_GATE],
    summary="Freeze a finished month (leaders of BI and Comercial)",
)
@inject
async def close(
    period: str = _PERIOD, principal: Principal = _principal, service: BudgetService = _service
) -> PeriodResponse:
    """Keeps every row, each rep's team and who had which customer; later reassignments do not
    change it."""

    item = await service.close(principal, period)
    return _period(item, False, True)


@router.post(
    "/{period}/reopen",
    response_model=PeriodResponse,
    dependencies=[_GATE],
    summary="Reopen a closed month (leaders of BI and Comercial)",
)
@inject
async def reopen(
    period: str = _PERIOD, principal: Principal = _principal, service: BudgetService = _service
) -> PeriodResponse:
    item = await service.reopen(principal, period)
    return _period(item, False, True)


@router.get(
    "/{period}/export",
    dependencies=[_GATE],
    summary="The month's board as a workbook (BI's template shape)",
    response_class=Response,
)
@inject
async def export(
    period: str = _PERIOD,
    regional: list[str] = Query(default=[], max_length=50),  # noqa: B008
    business_unit: list[str] = Query(default=[], max_length=50),  # noqa: B008
    team: list[str] = Query(default=[], max_length=100),  # noqa: B008
    salesperson: list[str] = Query(default=[], max_length=300),  # noqa: B008
    q: str | None = Query(default=None, max_length=80),
    principal: Principal = _principal,
    service: BudgetService = _service,
) -> Response:
    result = await service.export(
        principal, period, _filters(regional, business_unit, team, salesperson, q)
    )
    return _xlsx(result.content, result.filename)


__all__ = ["router"]
