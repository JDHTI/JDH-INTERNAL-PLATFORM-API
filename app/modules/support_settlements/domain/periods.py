"""Settlement periods: one calendar month, written ``YYYY-MM``."""

from __future__ import annotations

import re
from datetime import date

from app.core.errors import InvalidInputError

_PERIOD = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def bounds(period: str) -> tuple[date, date]:
    """The first day of the month and the first day of the next one.

    Raises:
        InvalidInputError: Not a ``YYYY-MM`` month.
    """

    found = _PERIOD.fullmatch((period or "").strip())
    if found is None:
        raise InvalidInputError("El periodo debe ser un mes AAAA-MM")
    year, month = int(found.group(1)), int(found.group(2))
    start = date(year, month, 1)
    following = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, following


def period_of(day: date) -> str:
    """The month of a day, ``YYYY-MM``."""

    return f"{day.year:04d}-{day.month:02d}"


def previous_period(day: date) -> str:
    """The month before the one of ``day``: the one a settlement closes (mes vencido)."""

    return period_of(
        date(day.year - 1, 12, 1) if day.month == 1 else date(day.year, day.month - 1, 1)
    )


def is_finished(period: str, today: date) -> bool:
    """Whether every day of the month has passed."""

    return bounds(period)[1] <= today


__all__ = ["bounds", "is_finished", "period_of", "previous_period"]
