"""The rules of Nivel de servicio: the KPIs of the area's board, the ABCD cuts and the periods."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.modules.service_level.domain.entities import (
    Bands,
    ClassValue,
    Cuts,
    Totals,
    class_counts,
    class_window,
    classify,
    kpis,
    month_chunks,
    pareto,
    served_ratio,
)


def pct(value: Decimal | None) -> Decimal:
    assert value is not None
    return round(value * 100, 1)


def test_the_kpis_match_the_whole_company_board() -> None:
    # The area's Power BI board (capture of 2026-11-26).
    result = kpis(
        Totals(
            orders=32_496,
            orders_pending=173,
            lines=149_588,
            lines_pending=218,
            qty=Decimal(1_925_742),
            qty_pending=Decimal(6_961),
            value=Decimal(25_911_977_763),
            value_pending=Decimal(52_518_101),
        )
    )

    assert pct(result.ns_orders) == Decimal("99.5")
    assert pct(result.ns_lines) == Decimal("99.9")
    assert pct(result.ns_qty) == Decimal("99.6")
    assert pct(result.ns_value) == Decimal("99.8")
    assert pct(result.ns_total) == Decimal("99.7")


def test_the_kpis_match_an_operation_center_board() -> None:
    result = kpis(
        Totals(
            orders=3_041,
            orders_pending=536,
            lines=14_451,
            lines_pending=713,
            qty=Decimal(179_652),
            qty_pending=Decimal(11_862),
            value=Decimal(2_556_902_886),
            value_pending=Decimal(131_208_339),
        )
    )

    assert [
        pct(v) for v in (result.ns_orders, result.ns_lines, result.ns_qty, result.ns_value)
    ] == [
        Decimal("82.4"),
        Decimal("95.1"),
        Decimal("93.4"),
        Decimal("94.9"),
    ]
    # (94.9 + 93.4 + 95.1) × 30 % + 82.4 × 10 %, from the unrounded levels.
    assert pct(result.ns_total) == Decimal("93.2")


def test_nothing_asked_has_no_service_level() -> None:
    result = kpis(Totals())

    assert result.ns_orders is None and result.ns_total is None
    assert served_ratio(Decimal(5), Decimal(4)) == 0


@pytest.mark.parametrize(
    ("cumulative", "klass"),
    [("0.80", "A"), ("0.8001", "B"), ("0.95", "B"), ("0.99", "C"), ("0.9901", "D")],
)
def test_the_abcd_cuts_close_each_class_inclusive(cumulative: str, klass: str) -> None:
    assert Cuts().of(Decimal(cumulative)) == klass


def test_cuts_and_bands_parse_from_settings() -> None:
    assert Cuts.parse("0.7, 0.9, 0.98") == Cuts(Decimal("0.7"), Decimal("0.9"), Decimal("0.98"))
    assert Bands.parse("0.96,0.90").of(Decimal("0.96")) == "ambar"
    assert Bands().of(Decimal("0.968")) == "verde"
    assert Bands().of(Decimal("0.899")) == "rojo"
    with pytest.raises(ValueError):
        Cuts.parse("0.95,0.80,0.99")
    with pytest.raises(ValueError):
        Bands.parse("0.90")


def test_products_rank_within_their_operation_center() -> None:
    values = [
        ClassValue("003", "big", Decimal(80)),
        ClassValue("003", "mid", Decimal(15)),
        ClassValue("003", "small", Decimal(4)),
        ClassValue("003", "tiny", Decimal(1)),
        ClassValue("001", "alone", Decimal(10)),
        ClassValue("001", "nothing", Decimal(0)),
    ]

    classes = {(item.co, item.key): item.klass for item in classify(values, Cuts())}

    assert classes == {
        ("003", "big"): "A",
        ("003", "mid"): "B",
        ("003", "small"): "C",
        ("003", "tiny"): "D",
        # Alone in its C.O.: 100 % of the value at once.
        ("001", "alone"): "D",
    }
    assert class_counts(classify(values, Cuts())) == {"A": 1, "B": 1, "C": 1, "D": 2}


def test_the_pareto_curve_starts_at_zero_and_ends_at_one() -> None:
    curve = pareto([Decimal(v) for v in (50, 30, 10, 5, 5, 0)])

    assert (curve[0].items, curve[0].value) == (0, 0)
    assert curve[1].value == Decimal("0.5") and curve[1].items == Decimal("0.2")
    assert (curve[-1].items, curve[-1].value) == (1, 1)
    assert len(pareto([Decimal(1)] * 1000)) == 101


def test_the_classes_read_the_closed_months() -> None:
    assert class_window(date(2026, 11, 26), 2) == (date(2026, 9, 1), date(2026, 10, 31))
    assert class_window(date(2026, 1, 3), 2) == (date(2025, 11, 1), date(2025, 12, 31))


def test_the_backfill_goes_a_month_at_a_time() -> None:
    assert month_chunks(date(2026, 9, 15), date(2026, 11, 3)) == [
        (date(2026, 9, 15), date(2026, 9, 30)),
        (date(2026, 10, 1), date(2026, 10, 31)),
        (date(2026, 11, 1), date(2026, 11, 3)),
    ]
