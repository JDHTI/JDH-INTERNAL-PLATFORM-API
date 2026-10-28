"""Report of a bulk load of supports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class RowIssue:
    """A problem of one row of the file (``row_number`` is the sheet row, header included)."""

    row_number: int
    field: str | None
    message: str


@dataclass(frozen=True)
class PreviewRow:
    """A row that would be created, in the fields people check first."""

    row_number: int
    supplier_code: str | None
    kind: str
    calc_method: str
    program: str | None
    customer_nit: str | None
    product_sku: str | None
    supplier_value: Decimal | None
    valid_from: date | None
    valid_to: date | None


@dataclass(frozen=True)
class BulkReport:
    """What a file would create (simulation) or did create (``applied``).

    The load is all or nothing: with any ``errors`` nothing is saved, even when asked to apply
    (``blocked``).
    """

    applied: bool
    blocked: bool
    sheet: str
    header_row: int
    rows_read: int
    valid: int
    invalid: int
    errors: list[RowIssue]
    errors_truncated: bool
    preview: list[PreviewRow]
    # Which header of the file feeds each field, and the headers nobody used.
    mapped_columns: dict[str, list[str]]
    ignored_columns: list[str]
    profile_used: bool
    created: list[UUID] = field(default_factory=list)
    with_conflicts: int = 0
