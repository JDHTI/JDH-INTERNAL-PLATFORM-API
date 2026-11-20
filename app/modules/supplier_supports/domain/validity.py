"""Validity of a support on a given day."""

from __future__ import annotations

import calendar
from datetime import date


def expiring_until(day: date) -> date:
    """The last day a support in force on ``day`` may end to be «por vencer» (2026-11-20):
    one calendar month later (the 31st of January gives the last day of February)."""

    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def is_expiring(valid_from: date | None, valid_to: date | None, day: date) -> bool:
    """Whether a support is in force on ``day`` and ends within :func:`expiring_until`."""

    return (
        validity_on(valid_from, valid_to, day) == "vigente"
        and valid_to is not None
        and valid_to <= expiring_until(day)
    )


def validity_on(valid_from: date | None, valid_to: date | None, day: date) -> str:
    """Whether a support is in force, over, or still to start on ``day``.

    A missing ``valid_to`` means open-ended (``INDEFINIDO`` / ``UFN`` in the
    workbook); a missing ``valid_from`` means in force since forever.

    Args:
        valid_from (date | None): First day in force.
        valid_to (date | None): Last day in force.
        day (date): Day to evaluate, in the business time zone.

    Returns:
        str: ``vigente``, ``vencido`` or ``programado``.
    """

    if valid_from is not None and day < valid_from:
        return "programado"
    if valid_to is not None and day > valid_to:
        return "vencido"
    return "vigente"


def overlaps(
    first_from: date | None, first_to: date | None, second_from: date | None, second_to: date | None
) -> bool:
    """Whether two date ranges share at least one day; ``None`` ends are unbounded."""

    starts_before_end = first_from is None or second_to is None or first_from <= second_to
    ends_after_start = second_from is None or first_to is None or second_from <= first_to
    return starts_before_end and ends_after_start
