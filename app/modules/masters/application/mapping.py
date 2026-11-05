"""Turn a warehouse row into a ``master_records`` row, following the master's fields."""

from __future__ import annotations

import math
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.modules.masters.domain.entities import MasterSpec
from app.modules.masters.domain.ports import Row

_SMALLINT = range(-32768, 32768)


def text(value: Any) -> str | None:
    """Trimmed text with collapsed whitespace, or ``None`` when blank."""

    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned or None


def active_flag(value: Any) -> bool | None:
    """The active flag of a row: ``A``/``I`` text or a boolean (``None`` when unknown)."""

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    flag = str(value).strip().upper()
    if flag == "A":
        return True
    if flag == "I":
        return False
    return None


def company_id(value: Any) -> int | None:
    """The company code when it fits the column (a small integer)."""

    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number in _SMALLINT else None


def json_safe(value: Any) -> Any:
    """A value ``attributes`` (JSONB) can store."""

    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return None
    if isinstance(value, str):
        return text(value)
    if isinstance(value, list | tuple):
        return [json_safe(item) for item in value]
    return str(value)


def record_row(spec: MasterSpec, row: Row) -> dict[str, Any] | None:
    """The record for one warehouse row; ``None`` when it has no key.

    Args:
        spec (MasterSpec): How the master's columns map to the record.
        row (Row): One row of the source relation.

    Returns:
        dict[str, Any] | None: ``source_key``, ``company_id``, ``code``, ``name``,
            ``is_active`` and ``attributes``.
    """

    key = text(row.get(spec.key))
    if key is None:
        return None
    return {
        "source_key": key,
        "company_id": company_id(row.get(spec.company)) if spec.company else None,
        "code": text(row.get(spec.code)) if spec.code else None,
        "name": text(row.get(spec.name)) if spec.name else None,
        "is_active": active_flag(row.get(spec.active)) if spec.active else None,
        "attributes": {column: json_safe(row.get(column)) for column in spec.attribute_columns},
    }
