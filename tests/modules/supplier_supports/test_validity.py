"""Validity of supports on a given day."""

from datetime import date

import pytest

from app.modules.supplier_supports.domain.validity import overlaps, validity_on

DAY = date(2026, 10, 9)


@pytest.mark.parametrize(
    ("valid_from", "valid_to", "expected"),
    [
        (date(2026, 1, 1), date(2026, 12, 31), "vigente"),
        (date(2026, 10, 9), date(2026, 10, 9), "vigente"),  # both ends are inclusive
        (date(2026, 1, 1), None, "vigente"),  # INDEFINIDO / UFN
        (None, None, "vigente"),
        (date(2026, 1, 1), date(2026, 10, 8), "vencido"),
        (date(2026, 10, 10), None, "programado"),
    ],
)
def test_validity_on(valid_from, valid_to, expected) -> None:
    assert validity_on(valid_from, valid_to, DAY) == expected


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ((date(2026, 7, 1), date(2027, 1, 30)), (date(2026, 9, 1), date(2026, 9, 30)), True),
        ((date(2026, 1, 1), date(2026, 3, 31)), (date(2026, 4, 1), date(2026, 6, 30)), False),
        ((date(2026, 1, 1), date(2026, 3, 31)), (date(2026, 3, 31), None), True),  # share a day
        ((None, None), (date(2026, 4, 1), date(2026, 6, 30)), True),
        ((date(2026, 1, 1), None), (None, date(2025, 12, 31)), False),
    ],
)
def test_overlaps(first, second, expected) -> None:
    assert overlaps(*first, *second) is expected
    assert overlaps(*second, *first) is expected
