"""Validity of a support on a given day."""

from __future__ import annotations

from datetime import date


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
