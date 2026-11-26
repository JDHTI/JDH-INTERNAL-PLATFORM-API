"""The board's rules: attribution, the three cuts and BI's totals."""

from __future__ import annotations

from decimal import Decimal

from app.modules.commercial_budget.domain import periods, rules
from app.modules.commercial_budget.domain.entities import NO_REP, Assignment, Budget
from tests.modules.commercial_budget.fakes import rep, sold

D = Decimal
REPS = {
    "101": rep("101", "Arismendy Betancur Viviana Maria"),
    "102": rep("102", "Giraldo Lopez Carlos Alberto"),
    "201": rep("201", "Peralta Valderrama Paula Andrea", team="Zona 1 Bogota", regional="CENTRO"),
}
ASSIGNMENTS = {
    "c1": Assignment("c1", "101", "Arismendy"),
    "c2": Assignment("c2", "101", "Arismendy"),
    "c3": Assignment("c3", "102", "Giraldo"),
    "c4": Assignment("c4", None),
}


def test_the_window_of_september() -> None:
    assert periods.previous_three("2026-09") == ("2026-06", "2026-07", "2026-08")
    assert periods.last_year("2026-09") == "2025-09"
    assert periods.previous_three("2026-02") == ("2025-11", "2025-12", "2026-01")
    assert periods.months_between("2025-11", "2026-02") == [
        "2025-11",
        "2025-12",
        "2026-01",
        "2026-02",
    ]


def test_sales_go_to_the_customers_current_rep_and_the_rest_is_shown_apart() -> None:
    month = rules.month_by_rep(
        [
            sold("c1", "1000", "800"),
            # Returns larger than the month's sales: subtract, and it is no customer.
            sold("c2", "-100", "-90"),
            sold("c3", "300", "240"),
            sold("c4", "50", "40"),
            sold("unknown", "70", "60"),
        ],
        ASSIGNMENTS,
    )

    assert month["101"].net == D(900) and month["101"].customers == 1
    # (900 − 710) / 900
    assert month["101"].margin == D("0.211111")
    assert month["102"].customers == 1
    assert month[NO_REP].net == D(120) and month[NO_REP].customers == 2


def test_the_average_skips_the_months_without_sales_like_excels_average() -> None:
    cut = rules.average(
        [
            rules.month_by_rep([sold("c1", "1000", "800")], ASSIGNMENTS)["101"],
            rules.month_by_rep([], ASSIGNMENTS).get("101", rules.EMPTY),
            rules.month_by_rep([sold("c1", "3000", "2700"), sold("c2", "1", "1")], ASSIGNMENTS)[
                "101"
            ],
        ]
    )

    assert cut.months == 2
    assert cut.net == D("2000.5")
    # The simple average of the monthly margins (20 % and ~10 %), as BI's template does.
    expected = (rules.margin(D(1000), D(800)) + rules.margin(D(3001), D(2701))) / 2
    assert cut.margin == expected.quantize(D("0.000001"))
    assert cut.customers == D("1.5")


def test_rows_cover_sales_budgets_and_the_roster() -> None:
    sales = {
        "2026-09": [sold("c1", "1000", "800"), sold("c3", "500", "400")],
        "2026-06": [sold("c1", "900", "720")],
        "2025-09": [sold("c3", "400", "300")],
    }
    budgets = {"201": Budget("2026-09", "201", budget_sales=D(700))}

    rows = rules.build_rows(
        "2026-09",
        sales=sales,
        assignments=ASSIGNMENTS,
        reps=REPS,
        budgets=budgets,
        roster_codes=["999"],
    )
    by_code = {row.code: row for row in rows}

    assert set(by_code) == {"101", "102", "201", "999"}
    assert by_code["101"].average.net == D(900) and by_code["101"].average.months == 1
    assert by_code["102"].last_year.net == D(400)
    assert by_code["201"].budget.budget_sales == D(700)
    assert by_code["201"].regional == "CENTRO"
    assert by_code["999"].name is None and by_code["999"].current.net == 0


def test_totals_weight_the_margin_like_bis_template() -> None:
    rows = rules.build_rows(
        "2026-09",
        sales={"2026-09": [sold("c1", "1000", "800"), sold("c3", "3000", "2700")]},
        assignments=ASSIGNMENTS,
        reps=REPS,
        budgets={
            "101": Budget("2026-09", "101", D(1200), D("0.20"), 10, D(1000)),
            "102": Budget("2026-09", "102", D(3600), D("0.10"), None, None),
        },
    )

    total = rules.totals(rows)

    assert total.current.net == D(4000) and total.current.customers == 2
    # (1000 × 20 % + 3000 × 10 %) / 4000
    assert total.current.margin == D("0.125")
    assert total.budget_sales == D(4800)
    # SUMPRODUCT(budget, margin) / SUM(budget)
    assert total.budget_margin == D("0.125")
    assert total.budget_customers == 10 and total.budget_collection == D(1000)


def test_variation_compliance_and_margin_gap() -> None:
    # Arismendy, September 2026 in BI's template: 680 M against 540.3 M last year.
    assert rules.variation(D(680_000_000), D(540_268_468)) == D("0.258634")
    assert rules.variation(D(100), D(0)) is None
    assert rules.variation(None, D(10)) is None
    assert rules.compliance(D(50), D(200)) == D("0.25")
    assert rules.compliance(D(50), None) is None
    assert rules.margin_gap(D("0.17"), D("0.18")) == D("-0.01")


def test_filters_take_several_values() -> None:
    rows = rules.build_rows(
        "2026-09", sales={}, assignments={}, reps=REPS, budgets={}, roster_codes=REPS
    )

    assert {r.code for r in rows if rules.matches(r, regionals=["CENTRO"])} == {"201"}
    assert {r.code for r in rows if rules.matches(r, teams=["Senior Medellin"])} == {"101", "102"}
    assert {r.code for r in rows if rules.matches(r, query="giraldo")} == {"102"}
    assert [key for key, _ in rules.group(rows, "regional")] == ["ANTIOQUIA", "CENTRO"]
