"""The rules of Costos: entry status, novelty status and previous prices."""

from datetime import date
from decimal import Decimal

import pytest

from app.modules.costs.domain.entries import (
    NO_QUOTATION,
    NOVELTY,
    OK,
    entry_status,
    fill_previous_prices,
    supplier_code,
    supplier_name,
)
from app.modules.costs.domain.novelties import (
    AWAITING_CREDIT_NOTE,
    DIFFERENCE,
    NO_LONGER,
    PENDING,
    RESOLVED,
    VOIDED,
    WITHOUT_QUOTATION,
    derive_status,
    kind_of,
)
from tests.modules.costs.fakes import line

D = Decimal


@pytest.mark.parametrize(
    ("price", "quotation", "expected"),
    [
        ("49384.67", "49384.67", OK),
        ("50050", "50000", OK),  # +0.1 %: on the threshold, still no novelty
        ("49950", "50000", OK),  # -0.1 %
        ("50051", "50000", NOVELTY),  # just past it
        ("49384.67", "50372.37", NOVELTY),  # the former app's false «Revisar» vs a later quote
        ("1000", None, NO_QUOTATION),
        ("1000", "0", NO_QUOTATION),
    ],
)
def test_an_entry_is_a_novelty_only_past_the_threshold(price, quotation, expected) -> None:
    assert (
        entry_status(D(price), None if quotation is None else D(quotation), D("0.001")) == expected
    )


def test_a_wider_threshold_is_configurable() -> None:
    assert entry_status(D("50400"), D("50000"), D("0.01")) == OK


def test_the_difference_and_its_percent_follow_the_quotation() -> None:
    entry = line(price="52500", quotation="50000", quantity="4")
    assert entry.difference == D("2500")
    assert entry.difference_pct == D("0.05")
    assert entry.amount == D("10000")
    assert line(quotation=None).difference is None


def test_the_commercial_supplier_label_splits_into_code_and_name() -> None:
    assert supplier_code("399 - UNILEVER ANDINA S.A") == "399"
    assert supplier_name("399 - UNILEVER ANDINA S.A") == "UNILEVER ANDINA S.A"
    assert supplier_code("SIN CODIGO") is None and supplier_name("SIN CODIGO") == "SIN CODIGO"
    assert supplier_code(None) is None


@pytest.mark.parametrize(
    ("kind", "voided", "needs", "note", "expected"),
    [
        (DIFFERENCE, False, None, False, PENDING),
        (WITHOUT_QUOTATION, False, None, False, PENDING),
        (NO_LONGER, False, None, False, RESOLVED),  # a quotation dated back resolved it
        (DIFFERENCE, False, True, False, AWAITING_CREDIT_NOTE),
        (DIFFERENCE, False, False, False, RESOLVED),
        (DIFFERENCE, False, True, True, RESOLVED),
        (DIFFERENCE, False, None, True, RESOLVED),
        (DIFFERENCE, True, True, True, VOIDED),  # voided wins over everything
    ],
)
def test_the_status_follows_from_what_the_novelty_has(kind, voided, needs, note, expected) -> None:
    status = derive_status(
        kind=kind, voided=voided, reason_needs_credit_note=needs, has_credit_note=note
    )
    assert status == expected


def test_kind_of_an_entry_status() -> None:
    assert kind_of(NOVELTY) == DIFFERENCE
    assert kind_of(NO_QUOTATION) == WITHOUT_QUOTATION
    assert kind_of(OK) == NO_LONGER


def test_previous_prices_inside_the_window_by_reference_and_unit() -> None:
    lines = [
        line("c", day=date(2026, 11, 3), price="12"),
        line("a", day=date(2026, 11, 1), price="10"),
        line("b", day=date(2026, 11, 2), price="11"),
        line("k", day=date(2026, 11, 2), price="99", unit="KL"),  # another unit, its own series
        line("d", day=date(2026, 11, 2), price="7", sku="2548"),
    ]

    filled = {item.line_key: item for item in fill_previous_prices(lines)}

    assert [item.line_key for item in fill_previous_prices(lines)] == ["c", "a", "b", "k", "d"]
    assert (filled["a"].previous_price, filled["a"].antepenultimate_price) == (None, None)
    assert (filled["b"].previous_price, filled["b"].antepenultimate_price) == (D("10"), None)
    assert (filled["c"].previous_price, filled["c"].antepenultimate_price) == (D("11"), D("10"))
    assert filled["k"].previous_price is None and filled["d"].previous_price is None


def test_two_entries_on_one_day_order_by_center_then_key() -> None:
    lines = [
        line("z", day=date(2026, 5, 11), price="2", co="009"),
        line("y", day=date(2026, 5, 11), price="1", co="001"),
    ]

    filled = {item.line_key: item for item in fill_previous_prices(lines)}

    assert filled["y"].previous_price is None and filled["z"].previous_price == D("1")
