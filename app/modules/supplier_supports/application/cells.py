"""Reading the cells of a file a person or a supplier filled in by hand.

Stricter than the workbook importer: here an unreadable cell is an error to fix, not a guess
left for review.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from app.modules.supplier_supports.application import normalize as n

_ISO_DATE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?$")
_EVERYTHING = {"TODOS", "TODAS", "TODO", "NACIONAL"}


def percent(raw: Any) -> Decimal:
    """A percentage as a fraction: ``0.18``, ``18``, ``18 %`` and ``18,5%`` are all read.

    Under 1 (in absolute value) it is already a fraction; from 1 to 100 it is in points.

    Raises:
        ValueError: If it is not a number or is outside -100..100.
    """

    text = str(raw).strip()
    explicit = text.endswith("%")
    value, issues = n.parse_number(text.removesuffix("%").strip() if explicit else raw, "valor")
    if value is None or issues:
        raise ValueError(f"no es un porcentaje: {raw!r}")
    if explicit or abs(value) >= 1:
        value = value / 100
    if abs(value) > 1:
        raise ValueError(f"porcentaje fuera de rango: {raw!r}")
    return value


def choice(raw: Any, allowed: tuple[str, ...]) -> str | None:
    """The closed value a cell spells (case, accents and spaces do not matter), if any."""

    key = n.fold(raw).replace(" ", "_")
    return key if key in allowed else None


def date_cell(raw: Any) -> Any:
    """ISO dates (``2026-10-01``, common in CSV) as dates; anything else is left to the reader."""

    if isinstance(raw, str) and (found := _ISO_DATE.match(raw)):
        year, month, day = (int(part) for part in found.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return raw
    return raw


def split_list(raw: Any) -> list[str]:
    """Values of a list cell or list: trimmed, upper-case, without repeats; ``TODOS`` = none."""

    parts = (
        [str(item) for item in raw]
        if isinstance(raw, (list, tuple))
        else re.split(r"[\n/,;]", str(raw))
    )
    values = list(dict.fromkeys(item.upper() for part in parts if (item := n.clean_text(part))))
    return [] if any(item in _EVERYTHING for item in values) else values
