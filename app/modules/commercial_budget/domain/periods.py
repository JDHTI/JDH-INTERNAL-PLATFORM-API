"""Budget periods: one calendar month, written ``YYYY-MM``."""

from __future__ import annotations

import re
from datetime import date

from app.core.errors import InvalidInputError

_PERIOD = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def parse(period: str) -> tuple[int, int]:
    """The year and month of a period.

    Raises:
        InvalidInputError: Not a ``YYYY-MM`` month.
    """

    found = _PERIOD.fullmatch((period or "").strip())
    if found is None:
        raise InvalidInputError("El periodo debe ser un mes AAAA-MM")
    return int(found.group(1)), int(found.group(2))


def bounds(period: str) -> tuple[date, date]:
    """The first day of the month and the first day of the next one."""

    year, month = parse(period)
    start = date(year, month, 1)
    following = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, following


def period_of(day: date) -> str:
    """The month of a day, ``YYYY-MM``."""

    return f"{day.year:04d}-{day.month:02d}"


def shift(period: str, months: int) -> str:
    """The month ``months`` away (negative goes back)."""

    year, month = parse(period)
    index = year * 12 + (month - 1) + months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def months_between(first: str, last: str) -> list[str]:
    """Every month from ``first`` to ``last``, both included (empty when reversed)."""

    found: list[str] = []
    current = first
    while current <= last:
        found.append(current)
        current = shift(current, 1)
    return found


def is_finished(period: str, today: date) -> bool:
    """Whether every day of the month has passed."""

    return bounds(period)[1] <= today


def previous_three(period: str) -> tuple[str, str, str]:
    """The three months before, oldest first (September: June, July, August)."""

    return shift(period, -3), shift(period, -2), shift(period, -1)


def last_year(period: str) -> str:
    """The same month a year before."""

    return shift(period, -12)


def window(period: str) -> tuple[str, ...]:
    """Every month a period's board reads: the month, the three before it and last year's."""

    return (period, *previous_three(period), last_year(period))


__all__ = [
    "bounds",
    "is_finished",
    "last_year",
    "months_between",
    "parse",
    "period_of",
    "previous_three",
    "shift",
    "window",
]
