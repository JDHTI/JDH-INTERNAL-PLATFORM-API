"""The month's board as a workbook shaped like BI's template (PLANTILLA PRESUPUESTO).

One row per rep, grouped by regional and team with their totals, and the company's total;
the real margin of a total is weighted by sales and the budget margin by budget, as BI does.
"""

from __future__ import annotations

import io
from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.modules.commercial_budget.domain import rules
from app.modules.commercial_budget.domain.entities import (
    BUDGET_FIELDS,
    CLOSED,
    STATUS_LABELS,
    Figures,
    RepRow,
    Totals,
)

_MONTHS = (
    "Ene",
    "Feb",
    "Mar",
    "Abr",
    "May",
    "Jun",
    "Jul",
    "Ago",
    "Sep",
    "Oct",
    "Nov",
    "Dic",
)
_HEAD = PatternFill("solid", fgColor="1F3864")
_TEAM = PatternFill("solid", fgColor="D9E1F2")
_REGIONAL = PatternFill("solid", fgColor="B4C6E7")
_COMPANY = PatternFill("solid", fgColor="8EA9DB")
_MONEY = "#,##0"
_PCT = "0.0%"
_COUNT = "#,##0"


def _label(period: str) -> str:
    year, month = period.split("-")
    return f"{year}/{_MONTHS[int(month) - 1]}"


def _figures(cut: Figures) -> list[Any]:
    return [
        float(cut.net),
        None if cut.margin is None else float(cut.margin),
        float(cut.customers),
    ]


def _decimal(value: Decimal | int | None) -> float | None:
    return None if value is None else float(value)


def _derived(current: Figures, last_year: Figures, budget_sales, budget_margin) -> list[Any]:
    return [
        _decimal(rules.variation(budget_sales, last_year.net)),
        _decimal(rules.compliance(current.net, budget_sales)),
        _decimal(rules.margin_gap(current.margin, budget_margin)),
    ]


def _rep_line(row: RepRow) -> list[Any]:
    budget = row.budget
    values = {name: getattr(budget, name) if budget else None for name in BUDGET_FIELDS}
    return [
        row.regional,
        row.business_unit,
        row.team,
        row.code,
        row.name,
        *_figures(row.last_year),
        *_figures(row.average),
        *_figures(row.current),
        *(_decimal(values[name]) for name in BUDGET_FIELDS),
        *_derived(row.current, row.last_year, values["budget_sales"], values["budget_margin"]),
    ]


def _total_line(label: str, totals: Totals) -> list[Any]:
    return [
        None,
        None,
        None,
        None,
        label,
        *_figures(totals.last_year),
        *_figures(totals.average),
        *_figures(totals.current),
        _decimal(totals.budget_sales),
        _decimal(totals.budget_margin),
        _decimal(totals.budget_customers),
        _decimal(totals.budget_collection),
        *_derived(totals.current, totals.last_year, totals.budget_sales, totals.budget_margin),
    ]


def board_workbook(view: Any) -> bytes:
    """The workbook of a :class:`BoardView`."""

    period = view.period.period
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Presupuesto"
    status = STATUS_LABELS.get(view.period.status, view.period.status)
    sheet.append([f"Presupuesto comercial {_label(period)} · {status}"])
    sheet["A1"].font = Font(bold=True, size=13)
    previous = ", ".join(_label(month) for month in view.previous)
    groups = [
        ("", 5),
        (_label(view.last_year), 3),
        (f"Promedio {previous}", 3),
        (_label(period), 3),
        (f"PPTO {_label(period)}", 4),
        ("", 3),
    ]
    header_one: list[Any] = []
    for title, span in groups:
        header_one += [title] + [None] * (span - 1)
    sheet.append(header_one)
    sheet.append(
        [
            "REGIONAL",
            "UNIDAD NEGOCIO",
            "EQUIPO",
            "CÓDIGO",
            "VENDEDOR",
            *(["$ VENTA", "% MARGEN", "# CLIENTES"] * 3),
            "PPTO VENTA",
            "PPTO % MARGEN",
            "PPTO # CLIENTES",
            "PPTO RECAUDO",
            "% VAR. VENTA LY",
            "% CUMPLIMIENTO",
            "MARGEN VS PPTO",
        ]
    )
    for row_index in (2, 3):
        for cell in sheet[row_index]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = _HEAD
            cell.alignment = Alignment(horizontal="center", wrap_text=True)
    column = 1
    for _, span in groups:
        if span > 1:
            sheet.merge_cells(
                start_row=2, start_column=column, end_row=2, end_column=column + span - 1
            )
        column += span
    by_regional: dict[str | None, dict[str | None, list[RepRow]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in view.rows:
        by_regional[row.regional][row.team].append(row)
    styled: list[tuple[int, PatternFill]] = []
    for regional in sorted(by_regional, key=lambda key: (key is None, key or "")):
        teams = by_regional[regional]
        for team in sorted(teams, key=lambda key: (key is None, key or "")):
            for row in teams[team]:
                sheet.append(_rep_line(row))
            sheet.append(
                _total_line(f"TOTAL {(team or 'SIN EQUIPO').upper()}", rules.totals(teams[team]))
            )
            styled.append((sheet.max_row, _TEAM))
        everyone: Sequence[RepRow] = [row for rows in teams.values() for row in rows]
        sheet.append(
            _total_line(f"TOTAL {(regional or 'SIN REGIONAL').upper()}", rules.totals(everyone))
        )
        styled.append((sheet.max_row, _REGIONAL))
    sheet.append(_total_line("TOTAL", view.totals))
    styled.append((sheet.max_row, _COMPANY))
    for index, fill in styled:
        for cell in sheet[index]:
            cell.fill = fill
            cell.font = Font(bold=True)
    money = (6, 9, 12, 15, 18)
    pct = (7, 10, 13, 16, 19, 20, 21)
    count = (8, 11, 14, 17)
    for index in range(4, sheet.max_row + 1):
        for position in money:
            sheet.cell(index, position).number_format = _MONEY
        for position in pct:
            sheet.cell(index, position).number_format = _PCT
        for position in count:
            sheet.cell(index, position).number_format = _COUNT
    widths = [14, 18, 24, 10, 36] + [15, 10, 10] * 3 + [15, 10, 10, 15, 11, 11, 11]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = "F4"
    if view.period.status == CLOSED and view.period.closed_at:
        sheet.append([])
        sheet.append(
            [f"Cerrado por {view.period.closed_by} el {view.period.closed_at:%Y-%m-%d %H:%M} UTC"]
        )
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


__all__ = ["board_workbook"]
