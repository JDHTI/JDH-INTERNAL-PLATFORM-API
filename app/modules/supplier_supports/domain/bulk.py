"""Report of a bulk load of supports, and the key that matches a row to a stored support."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.modules.supplier_supports.domain.entities import SupportData

# What a row of the file does to the master.
CREATE = "crear"
UPDATE = "actualizar"
UNCHANGED = "sin_cambio"
ACTIONS = (CREATE, UPDATE, UNCHANGED)

# Fields that never make two versions different: where the row came from.
_TRACE_FIELDS = frozenset({"source", "source_key"})
# Owned by the JDH approval panel once set.
JDH_FIELDS = frozenset({"jdh_pct", "jdh_net_cost"})


def support_key(data: SupportData) -> tuple[Any, ...]:
    """The natural key of a support: supplier + client (NIT) + JDH SKU + start date (BI,
    2026-11-18).

    The class, the agreement number and the supplier's SKU are not part of it: a row that
    changes them updates the same support. The supplier's SKU only stands in for a JDH SKU
    the master could not resolve. The start date is part of the key, so a renewal with a new
    start is a new support and the previous one is closed; changing only the end date is an
    update.
    """

    return (
        data.commercial_supplier_id,
        data.customer_nit,
        data.product_sku,
        None if data.product_sku else data.supplier_sku,
        data.valid_from,
    )


def same_support(left: SupportData, right: SupportData) -> bool:
    """Whether two versions say the same (the trace of the row does not count)."""

    return all(
        getattr(left, item.name) == getattr(right, item.name)
        for item in fields(SupportData)
        if item.name not in _TRACE_FIELDS
    )


def merge(stored: SupportData, incoming: SupportData, provided: frozenset[str]) -> SupportData:
    """The stored support with what the file brings.

    Only the fields the row (or the load's defaults) gave are replaced, plus what resolving
    the masters decides (ids, review state); a field the file does not carry keeps its value,
    so a re-upload without the JDH column never wipes an approved JDH contribution. Names and
    units completed from the masters are only taken when the stored support had none.

    The JDH contribution belongs to the approval panel (``/supplier-support-requests``): a
    file only fills it on a support that has none, it never replaces one already set.
    """

    changes: dict[str, Any] = {
        name: getattr(incoming, name)
        for name in provided
        if not (name in JDH_FIELDS and getattr(stored, name) is not None)
    }
    for name in ("commercial_supplier_id", "product_id", "status", "issues", "source"):
        changes[name] = getattr(incoming, name)
    for name in ("brand", "customer_name", "pack_factor"):
        if getattr(stored, name) is None and name not in provided:
            changes[name] = getattr(incoming, name)
    if "value_unit" in provided:
        changes["unit_basis_source"] = incoming.unit_basis_source
    elif stored.value_unit is None:
        changes["value_unit"] = incoming.value_unit
        changes["unit_basis_source"] = incoming.unit_basis_source
    return replace(stored, **changes)


@dataclass(frozen=True)
class RowIssue:
    """A problem of one row of the file (``row_number`` is the sheet row, header included)."""

    row_number: int
    field: str | None
    message: str


@dataclass(frozen=True)
class PreviewRow:
    """A row of the file in the fields people check first, and what it does."""

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
    action: str = CREATE
    support_id: UUID | None = None


@dataclass(frozen=True)
class MissingSupport:
    """A stored support of the file's suppliers and classes that the file no longer brings."""

    support_id: UUID
    customer_nit: str | None
    product_sku: str | None
    valid_from: date | None
    valid_to: date | None
    # Whether the load closes it (``expire_missing`` and it is in force).
    expires: bool


@dataclass(frozen=True)
class BulkReport:
    """What a file would do (simulation) or did (``applied``).

    The load is all or nothing: with any ``errors`` nothing is saved, even when asked to apply
    (``blocked``). Each valid row creates a support, updates the one with its key or leaves it
    as it is.
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
    # What the load does (or did) by key.
    to_create: int = 0
    to_update: int = 0
    unchanged: int = 0
    missing: int = 0
    to_expire: int = 0
    missing_preview: list[MissingSupport] = field(default_factory=list)
    # Rules v2: supports the load expires or voids (crossed by a row with ``on_overlap``, or
    # ``Validación = No``), rows a blank ``Validación`` leaves alone, and the supports the
    # rows of a business group became.
    to_retire: int = 0
    retired: list[UUID] = field(default_factory=list)
    skipped: int = 0
    group_supports: int = 0
    updated: list[UUID] = field(default_factory=list)
    expired: list[UUID] = field(default_factory=list)
    load_id: UUID | None = None
    # A big load is published to the DWH from this moment (its quiet hours).
    publish_not_before: datetime | None = None
    # Applied: supports of the file (created, updated or unchanged, with a JDH SKU) whose DWH
    # cost the load saved, and those the DWH has no cost for.
    costs_assigned: int = 0
    costs_missing: int = 0
