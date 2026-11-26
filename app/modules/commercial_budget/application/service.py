"""The commercial budget by sales rep: the month's board, the budget, close and reopen.

- ``read`` on ``presupuesto-comercial`` looks; ``manage`` fills the budget (by hand or with
  the workbook). BI and Comercial hold it by role (2026-11-26). The leaders of BI and Comercial
  (and platform administrators) close and reopen a month.
- Each person sees the reps of their sales scope (``salespeople``): BI, the directors and the
  area leaders every rep; a coordinator their team; a rep themself. The budget of a rep out of
  the scope cannot be changed.
- An open month is live: today's assignment of customers to reps, its comparisons too. A
  closed month is its frozen picture.
- The sales are copied by month from the warehouse (``consumo.ventas`` grouped by customer
  branch) by the daily job, so the board never waits for the warehouse.
"""

from __future__ import annotations

import asyncio
import logging
import unicodedata
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, PayloadTooLargeError
from app.modules.commercial_budget.domain import periods, rules
from app.modules.commercial_budget.domain.entities import (
    BUDGET_FIELDS,
    CLOSED,
    FILE,
    MANUAL,
    NO_REP,
    Assignment,
    Budget,
    BudgetChange,
    BudgetFileRow,
    BudgetPeriod,
    MonthLoad,
    RepRow,
    Snapshot,
    Totals,
)
from app.modules.commercial_budget.domain.ports import (
    BudgetRepository,
    LeaderPolicy,
    Policy,
    SalesWarehouse,
    ScopeResolver,
)
from app.modules.identity.domain.principal import Principal

logger = logging.getLogger(__name__)
BOGOTA = ZoneInfo("America/Bogota")
SYSTEM_ACTOR = "presupuesto-automatico"
MAX_ITEMS = 1000
MAX_MARGIN = Decimal(1)
MIN_MARGIN = Decimal(-1)


@dataclass(frozen=True)
class Permissions:
    can_read: bool
    can_manage: bool
    can_close: bool
    # ``all``, ``team``, ``mine`` or ``none`` (the sales scope).
    scope: str


@dataclass(frozen=True)
class PeriodView:
    period: BudgetPeriod
    is_current: bool
    is_finished: bool


@dataclass(frozen=True)
class Filters:
    regionals: Sequence[str] = ()
    business_units: Sequence[str] = ()
    teams: Sequence[str] = ()
    salespeople: Sequence[str] = ()
    query: str | None = None


@dataclass(frozen=True)
class BoardView:
    """A month's board for one person, filtered."""

    period: BudgetPeriod
    is_current: bool
    is_finished: bool
    # The months behind each cut.
    previous: tuple[str, str, str]
    last_year: str
    rows: list[RepRow]
    totals: Totals
    groups: Mapping[str, list[tuple[str | None, Totals]]]
    # Subtotals per regional and team, for the regional → team → rep table.
    team_groups: list[tuple[str | None, str | None, Totals]]
    # Filter options over the person's whole scope (not only the filtered rows).
    options: Mapping[str, list[Any]]
    # Months of the window not copied from the warehouse yet (an open month only).
    missing_months: list[str]
    # When the month's sales were last copied (an open month only).
    sales_loaded_at: datetime | None


@dataclass(frozen=True)
class UploadProblem:
    row: int
    value: str | None
    reason: str


@dataclass(frozen=True)
class UploadReport:
    applied: bool
    rows: int
    matched: int
    changed: int
    unchanged: int
    problems: list[UploadProblem]
    warnings: list[UploadProblem]
    budgets: list[Budget] = field(default_factory=list)


@dataclass(frozen=True)
class ExportFile:
    content: bytes
    filename: str


class BudgetService:
    """The commercial budget by sales rep."""

    def __init__(
        self,
        repository: BudgetRepository,
        warehouse: SalesWarehouse,
        policy: Policy,
        leaders: LeaderPolicy,
        scopes: ScopeResolver,
        start_period: str = "2026-01",
        backfill_months: int = 6,
        max_upload_bytes: int = 10 * 1024 * 1024,
        writer: Callable[..., bytes] | None = None,
        template_writer: Callable[..., bytes] | None = None,
        reader: Callable[[bytes], list[BudgetFileRow]] | None = None,
        today: Callable[[], date] | None = None,
    ) -> None:
        """Create the service.

        Args:
            repository (BudgetRepository): The platform's tables.
            warehouse (SalesWarehouse): Sales and the rep master from the warehouse.
            policy (Policy): ``presupuesto-comercial``.
            leaders (LeaderPolicy): Who closes and reopens a month.
            scopes (ScopeResolver): Each person's sales scope.
            start_period (str): First month the board offers; sales are copied from a year
                before it (its comparison with last year).
            backfill_months (int): Missing months the daily job copies per run.
            max_upload_bytes (int): Largest budget workbook.
            writer (Callable[..., bytes] | None): Builds the board's workbook.
            template_writer (Callable[..., bytes] | None): Builds the budget template.
            reader (Callable[[bytes], list[BudgetFileRow]] | None): Reads a budget workbook.
            today (Callable[[], date] | None): The day in Bogotá (tests).
        """

        periods.parse(start_period)
        self.repository = repository
        self.warehouse = warehouse
        self.policy = policy
        self.leaders = leaders
        self.scopes = scopes
        self.start_period = start_period
        self.backfill_months = max(0, backfill_months)
        self.max_upload_bytes = max_upload_bytes
        self.writer = writer
        self.template_writer = template_writer
        self.reader = reader
        self._today = today or (lambda: datetime.now(BOGOTA).date())

    # --- access ---

    async def permissions(self, principal: Principal) -> Permissions:
        manage = await self.policy.is_manager(principal)
        read = manage or await self.policy.can_read(principal)
        scope = (await self.scopes.scope_for(principal)).scope if read else "none"
        return Permissions(read, manage, read and await self.leaders.is_manager(principal), scope)

    async def _require_read(self, principal: Principal) -> None:
        if not await self.policy.can_read(principal) and not await self.policy.is_manager(
            principal
        ):
            raise ForbiddenError("No tienes permiso para consultar el presupuesto comercial")

    async def _require_manage(self, principal: Principal) -> None:
        if not await self.policy.is_manager(principal):
            raise ForbiddenError("No tienes permiso para diligenciar el presupuesto comercial")

    async def _require_leader(self, principal: Principal, action: str) -> None:
        if not await self.leaders.is_manager(principal):
            raise ForbiddenError(f"Solo los líderes de BI y de Comercial {action} un mes")

    def _last_period(self) -> str:
        """The latest month offered: next month, so its budget can be filled in advance."""

        return periods.shift(periods.period_of(self._today()), 1)

    def _check_period(self, period: str) -> str:
        period = period.strip()
        periods.parse(period)
        if period < self.start_period:
            raise InvalidInputError(f"El presupuesto comercial empieza en {self.start_period}")
        if period > self._last_period():
            raise InvalidInputError("Solo se puede preparar el presupuesto hasta el mes siguiente")
        return period

    # --- months ---

    async def periods(self, principal: Principal) -> list[PeriodView]:
        """Every month from the start to next month, the newest first."""

        await self._require_read(principal)
        today = self._today()
        current = periods.period_of(today)
        stored = {item.period: item for item in await self.repository.periods()}
        return [
            PeriodView(
                period=stored.get(month) or BudgetPeriod(month),
                is_current=month == current,
                is_finished=periods.is_finished(month, today),
            )
            for month in reversed(periods.months_between(self.start_period, self._last_period()))
        ]

    # --- the board ---

    async def live_rows(self, period: str) -> tuple[list[RepRow], dict, dict[str, Assignment]]:
        """Every rep of an open month, unscoped: rows, the window's sales and assignments."""

        window = periods.window(period)
        sales = await self.repository.sales(window)
        assignments = await self.repository.assignments()
        reps = await self.repository.reps()
        budgets = await self.repository.budgets(period)
        roster = [
            code for code, rep in reps.items() if rep.is_active and rep.business_unit is not None
        ]
        rows = rules.build_rows(
            period,
            sales=sales,
            assignments=assignments,
            reps=reps,
            budgets=budgets,
            roster_codes=roster,
        )
        return rows, sales, assignments

    async def _scoped_rows(
        self, principal: Principal, period: str
    ) -> tuple[BudgetPeriod, list[RepRow], dict[str, MonthLoad]]:
        stored = await self.repository.period(period)
        loads: dict[str, MonthLoad] = {}
        if stored.status == CLOSED:
            rows = await self.repository.snapshot(period)
        else:
            rows, _, _ = await self.live_rows(period)
            loads = await self.repository.loads()
        scope = await self.scopes.scope_for(principal)
        if scope.codes is not None:
            rows = [row for row in rows if row.code != NO_REP and scope.allows(row.code)]
        return stored, rows, loads

    async def board(
        self, principal: Principal, period: str, filters: Filters | None = None
    ) -> BoardView:
        """A month's board: the person's reps, filtered, with totals and groups."""

        await self._require_read(principal)
        period = self._check_period(period)
        filters = filters or Filters()
        stored, scoped, loads = await self._scoped_rows(principal, period)
        rows = [
            row
            for row in scoped
            if rules.matches(
                row,
                regionals=filters.regionals,
                business_units=filters.business_units,
                teams=filters.teams,
                salespeople=filters.salespeople,
                query=filters.query,
            )
        ]
        today = self._today()
        window = periods.window(period)
        reachable = [month for month in window if month <= periods.period_of(today)]
        loaded = loads.get(period)
        return BoardView(
            period=stored,
            is_current=period == periods.period_of(today),
            is_finished=periods.is_finished(period, today),
            previous=periods.previous_three(period),
            last_year=periods.last_year(period),
            rows=rows,
            totals=rules.totals(rows),
            groups={by: rules.group(rows, by) for by in ("regional", "business_unit", "team")},
            team_groups=rules.group_teams(rows),
            options=_options(scoped),
            missing_months=(
                [] if stored.status == CLOSED else [m for m in reachable if m not in loads]
            ),
            sales_loaded_at=loaded.loaded_at if loaded else None,
        )

    # --- the budget ---

    async def _editable(self, principal: Principal, period: str) -> tuple[str, Any]:
        await self._require_manage(principal)
        period = self._check_period(period)
        if (await self.repository.period(period)).status == CLOSED:
            raise ConflictError(f"El presupuesto de {period} está cerrado; reábrelo para editarlo")
        return period, await self.scopes.scope_for(principal)

    async def update_budgets(
        self,
        principal: Principal,
        period: str,
        items: Sequence[Mapping[str, Any]],
        note: str | None = None,
    ) -> list[Budget]:
        """Write the fields sent of each rep's budget (``null`` clears one).

        Each item is ``{salesperson_code, version?, <field>: value...}``; a field left out does
        not change. ``version`` (the one the person saw) catches a concurrent edit.

        Raises:
            ConflictError: The month is closed, or a budget changed since it was read.
            ForbiddenError: A rep out of the person's scope.
            InvalidInputError: An unknown rep, a value out of range, or nothing to change.
        """

        period, scope = await self._editable(principal, period)
        if not items:
            raise InvalidInputError("No hay cambios")
        if len(items) > MAX_ITEMS:
            raise InvalidInputError(f"Máximo {MAX_ITEMS} vendedores por cambio")
        reps = await self.repository.reps()
        changes: dict[str, dict[str, Any]] = {}
        versions: dict[str, int] = {}
        for item in items:
            code = str(item.get("salesperson_code") or "").strip()
            if not code or code == NO_REP:
                raise InvalidInputError("Falta el código del vendedor")
            if code not in reps:
                raise InvalidInputError(f"El vendedor {code} no está en el maestro de vendedores")
            if not scope.allows(code):
                raise ForbiddenError(f"El vendedor {code} no está en tu alcance")
            values = {name: item[name] for name in BUDGET_FIELDS if name in item}
            if not values:
                continue
            changes.setdefault(code, {}).update(
                {name: _check_value(name, value) for name, value in values.items()}
            )
            if item.get("version") is not None:
                versions[code] = int(item["version"])
        if not changes:
            raise InvalidInputError("No hay cambios")
        return await self.repository.save_budgets(
            period,
            changes,
            versions,
            actor=principal.email,
            source=MANUAL,
            note=_note(note),
            now=datetime.now(UTC),
        )

    async def changes(
        self, principal: Principal, period: str, salesperson_code: str | None, limit: int = 100
    ) -> list[BudgetChange]:
        """The history of the month's budget (the person's scope)."""

        await self._require_read(principal)
        period = self._check_period(period)
        scope = await self.scopes.scope_for(principal)
        found = await self.repository.changes(
            period, (salesperson_code or "").strip() or None, max(1, min(limit, 500))
        )
        return [item for item in found if scope.allows(item.salesperson_code)]

    async def budget_template(self, principal: Principal, period: str) -> ExportFile:
        """The budget workbook of the month, filled with the person's reps and their budget."""

        await self._require_read(principal)
        if self.template_writer is None:
            raise InvalidInputError("La plantilla no está disponible")
        period = self._check_period(period)
        _, rows, _ = await self._scoped_rows(principal, period)
        rows = [row for row in rows if row.code != NO_REP]
        return ExportFile(
            self.template_writer(period, rows), f"presupuesto-comercial-{period}-plantilla.xlsx"
        )

    async def upload_budgets(
        self,
        principal: Principal,
        period: str,
        filename: str | None,
        content: bytes,
        *,
        apply: bool,
        note: str | None = None,
    ) -> UploadReport:
        """Read a budget workbook; simulate it, or apply it with ``apply``.

        A row is found by ``CÓDIGO VENDEDOR``, or by the rep's name when the file has no code
        (BI's ``Ppto`` file); a blank cell leaves its field as it is.
        """

        period, scope = await self._editable(principal, period)
        if self.reader is None:
            raise InvalidInputError("La carga no está disponible")
        if not (filename or "").lower().endswith(".xlsx"):
            raise InvalidInputError("El archivo debe ser un .xlsx")
        if len(content) > self.max_upload_bytes:
            raise PayloadTooLargeError(
                f"El archivo supera {self.max_upload_bytes // (1024 * 1024)} MB"
            )
        parsed = self.reader(content)
        reps = await self.repository.reps()
        current = await self.repository.budgets(period)
        by_name: dict[str, list[str]] = {}
        for code, rep in reps.items():
            if rep.name:
                by_name.setdefault(_normal(rep.name), []).append(code)
        problems: list[UploadProblem] = []
        warnings: list[UploadProblem] = []
        changes: dict[str, dict[str, Any]] = {}
        seen: dict[str, int] = {}
        matched = unchanged = 0
        for item in parsed:
            label = item.code or item.name
            if item.problems:
                problems.extend(UploadProblem(item.row, label, text) for text in item.problems)
                continue
            code = item.code if item.code in reps else None
            if code is None and item.code:
                problems.append(
                    UploadProblem(item.row, item.code, "El código no está en el maestro")
                )
                continue
            if code is None:
                candidates = by_name.get(_normal(item.name or ""), [])
                if len(candidates) != 1:
                    reason = (
                        "Hay varios vendedores con ese nombre; usa el código"
                        if candidates
                        else "No se encontró el vendedor; usa el código"
                    )
                    problems.append(UploadProblem(item.row, item.name, reason))
                    continue
                code = candidates[0]
                warnings.append(
                    UploadProblem(item.row, item.name, f"Se encontró por nombre: código {code}")
                )
            if code in seen:
                problems.append(
                    UploadProblem(item.row, label, f"El vendedor ya viene en la fila {seen[code]}")
                )
                continue
            seen[code] = item.row
            if not scope.allows(code):
                problems.append(UploadProblem(item.row, label, "El vendedor no está en tu alcance"))
                continue
            matched += 1
            before = current.get(code)
            wanted = {
                name: value
                for name, value in item.values.items()
                if before is None or getattr(before, name) != value
            }
            if wanted:
                changes[code] = wanted
            else:
                unchanged += 1
        saved: list[Budget] = []
        if apply and changes:
            saved = await self.repository.save_budgets(
                period,
                changes,
                {},
                actor=principal.email,
                source=FILE,
                note=_note(note) or (filename or "").strip()[:200] or None,
                now=datetime.now(UTC),
            )
        return UploadReport(
            applied=apply and bool(changes),
            rows=len(parsed),
            matched=matched,
            changed=len(changes),
            unchanged=unchanged,
            problems=problems,
            warnings=warnings,
            budgets=saved,
        )

    # --- close ---

    async def close(self, principal: Principal, period: str) -> BudgetPeriod:
        """Freeze a finished month: every row, each rep's team and who had which customer.

        Raises:
            InvalidInputError: The month has not finished, or its sales are not copied.
            ConflictError: It is closed already, or the budget changed while closing.
        """

        await self._require_leader(principal, "cierran")
        period = self._check_period(period)
        if not periods.is_finished(period, self._today()):
            raise InvalidInputError("Solo se cierra un mes terminado")
        stored = await self.repository.period(period)
        if stored.status == CLOSED:
            raise ConflictError(f"El presupuesto de {period} ya está cerrado")
        if period not in await self.repository.loads():
            raise InvalidInputError(
                f"Las ventas de {period} no se han copiado del DWH; sincroniza antes de cerrar"
            )
        rows, sales, assignments = await self.live_rows(period)
        customers = [
            (
                assignments.get(item.customer_key) or Assignment(item.customer_key, None, None),
                item,
            )
            for item in sales.get(period, ())
        ]
        return await self.repository.close(
            period,
            Snapshot(rows=rows, customers=customers),
            stored.version,
            principal.email,
            datetime.now(UTC),
        )

    async def reopen(self, principal: Principal, period: str) -> BudgetPeriod:
        """Put a closed month back to live (its picture is dropped)."""

        await self._require_leader(principal, "reabren")
        return await self.repository.reopen(
            self._check_period(period), principal.email, datetime.now(UTC)
        )

    # --- export ---

    async def export(
        self, principal: Principal, period: str, filters: Filters | None = None
    ) -> ExportFile:
        """The month's board as a workbook shaped like BI's template."""

        if self.writer is None:
            raise InvalidInputError("La descarga no está disponible")
        view = await self.board(principal, period, filters)
        content = self.writer(view)
        return ExportFile(content, f"presupuesto-comercial-{view.period.period}.xlsx")

    # --- sales copy (daily job and by hand) ---

    async def refresh(self) -> Mapping[str, Any]:
        """Copy the rep master and the sales: the current month, the previous one when not
        closed, and up to ``backfill_months`` missing months of the history (the newest
        first), from a year before the start."""

        today = self._today()
        now = datetime.now(UTC)
        reps = await self.warehouse.reps()
        saved_reps = await self.repository.replace_reps(reps, now)
        current = periods.period_of(today)
        previous = periods.shift(current, -1)
        loads = await self.repository.loads()
        targets = [current]
        if (await self.repository.period(previous)).status != CLOSED:
            targets.append(previous)
        history = periods.months_between(periods.shift(self.start_period, -12), current)
        missing = [month for month in reversed(history) if month not in loads]
        targets += [month for month in missing if month not in targets][: self.backfill_months]
        done: dict[str, Any] = {}
        for month in targets:
            start, following = periods.bounds(month)
            sales = await self.warehouse.month(start, following)
            load = await self.repository.replace_month(month, sales, datetime.now(UTC))
            done[month] = {"clientes": load.customers, "lineas": load.lines, "venta": str(load.net)}
        pending = [month for month in missing if month not in done]
        return {"vendedores": saved_reps, "meses": done, "pendientes": pending}

    async def sync(self, principal: Principal) -> Mapping[str, Any]:
        """Run the copy now (``manage``); it goes on even if the request is cancelled."""

        await self._require_manage(principal)
        return await _detached(self.refresh())


_RUNNING: set[asyncio.Task] = set()


async def _detached(work: Coroutine[Any, Any, Any]) -> Any:
    """Run the copy to the end even if the client hangs up (minutes reading the DWH)."""

    task = asyncio.ensure_future(work)
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)
    return await asyncio.shield(task)


def _options(rows: Sequence[RepRow]) -> dict[str, list[Any]]:
    def values(name: str) -> list[str]:
        return sorted({getattr(row, name) for row in rows if getattr(row, name)})

    return {
        "regionals": values("regional"),
        "business_units": values("business_unit"),
        "teams": values("team"),
        "salespeople": sorted(
            ((row.code, row.name, row.team) for row in rows if row.code != NO_REP),
            key=lambda item: (item[1] or item[0]).lower(),
        ),
    }


def _check_value(name: str, value: Any) -> Decimal | int | None:
    """A budget field's value, validated (``None`` clears it)."""

    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise InvalidInputError(f"Valor inválido en {name}")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise InvalidInputError(f"Valor inválido en {name}: {value}") from exc
    if not number.is_finite():
        raise InvalidInputError(f"Valor inválido en {name}")
    if name == "budget_margin":
        if not MIN_MARGIN <= number <= MAX_MARGIN:
            raise InvalidInputError("El margen va como fracción entre -1 y 1 (0,18 = 18 %)")
        return number
    if number < 0:
        raise InvalidInputError(f"El valor de {name} no puede ser negativo")
    if name == "budget_customers":
        if number != number.to_integral_value():
            raise InvalidInputError("Los clientes van en números enteros")
        return int(number)
    return number


def _note(note: str | None) -> str | None:
    text = (note or "").strip()
    return text[:500] or None


def _normal(text: str) -> str:
    """A name compared without accents, case or extra spaces."""

    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(plain.lower().split())


__all__ = [
    "BoardView",
    "BudgetService",
    "ExportFile",
    "Filters",
    "PeriodView",
    "Permissions",
    "UploadProblem",
    "UploadReport",
]
