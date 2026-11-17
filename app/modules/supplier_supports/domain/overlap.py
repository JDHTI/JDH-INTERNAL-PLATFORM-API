"""Supports that cross: same class and scope, with validities that overlap (rules v2).

Two supports of the same class for the same supplier, customer (and branch), reference and
scope cannot be in force on the same day. Creating one that crosses an existing support is
refused unless the person decides what happens to the previous one: it expires the day before
the new one starts (``vencer_anterior``) or it is voided (``anular_anterior``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from app.modules.supplier_supports.domain.entities import Support, SupportData

EXPIRE_PREVIOUS = "vencer_anterior"
VOID_PREVIOUS = "anular_anterior"
OVERLAP_ACTIONS = (EXPIRE_PREVIOUS, VOID_PREVIOUS)


@dataclass(frozen=True)
class Supersede:
    """What happens to a support another one replaces (or a mass void / expiry).

    ``valid_to`` set: it expires that day, unless it starts later (a programmed support
    cannot end before it starts): then it is voided. ``valid_to`` ``None``: it is voided with
    ``reason``. ``version`` ``None`` takes the current version (a mass action by filter).
    """

    support_id: UUID
    version: int | None
    valid_to: date | None
    reason: str


def _upper(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(value.upper() for value in values))


def same_scope(left: SupportData, right: SupportData) -> bool:
    """Same class, supplier, customer, branch, group, reference, warehouse and scope lists."""

    if left.kind != right.kind or left.commercial_supplier_id != right.commercial_supplier_id:
        return False
    for name in ("customer_nit", "customer_branch_id", "warehouse"):
        if (getattr(left, name) or "").upper() != (getattr(right, name) or "").upper():
            return False
    # With a NIT the business group is only a label (a group's support is one per NIT): the
    # group decides the scope only of a support without a customer.
    if (
        not left.customer_nit
        and (left.customer_group or "").upper() != (right.customer_group or "").upper()
    ):
        return False
    if left.product_sku or right.product_sku:
        if left.product_sku != right.product_sku:
            return False
    elif (left.supplier_sku or "").upper() != (right.supplier_sku or "").upper():
        return False
    return all(
        _upper(getattr(left, name)) == _upper(getattr(right, name))
        for name in ("regionals", "channels", "business_units")
    )


def dates_cross(left: SupportData, right: SupportData) -> bool:
    """Validities that share at least one day (an empty end is open)."""

    start = max(left.valid_from or date.min, right.valid_from or date.min)
    end = min(left.valid_to or date.max, right.valid_to or date.max)
    return start <= end


def is_live(data: SupportData) -> bool:
    """Not voided and switched on: the only supports that can cross another."""

    return data.status != "anulado" and data.is_enabled


def overlaps(new: SupportData, other: SupportData) -> bool:
    """Whether ``other`` (a stored support) crosses ``new``."""

    return is_live(other) and same_scope(new, other) and dates_cross(new, other)


def supersede(
    new: SupportData, crossed: Sequence[Support], action: str, reason: str | None = None
) -> list[Supersede]:
    """What to do with each crossed support so ``new`` can be saved.

    Expiring ends a previous support the day before ``new`` starts. One that starts that same
    day or later cannot end before it starts: it is voided instead.

    Raises:
        ValueError: If ``action`` is unknown.
    """

    if action not in OVERLAP_ACTIONS:
        raise ValueError(action)
    note = reason or "Reemplazado por un apoyo nuevo que se cruza"
    plan: list[Supersede] = []
    for support in crossed:
        last_day = new.valid_from - timedelta(days=1) if new.valid_from else None
        starts = support.data.valid_from or date.min
        if action == EXPIRE_PREVIOUS and last_day is not None and starts <= last_day:
            plan.append(Supersede(support.id, support.version, last_day, note))
        else:
            plan.append(Supersede(support.id, support.version, None, note))
    return plan


def describe(support: Support) -> dict[str, object]:
    """A crossed support as the 409 lists it."""

    data = support.data
    return {
        "id": str(support.id),
        "version": support.version,
        "kind": data.kind,
        "calc_method": data.calc_method,
        "customer_nit": data.customer_nit,
        "customer_branch_id": data.customer_branch_id,
        "product_sku": data.product_sku,
        "supplier_sku": data.supplier_sku,
        "valid_from": data.valid_from.isoformat() if data.valid_from else None,
        "valid_to": data.valid_to.isoformat() if data.valid_to else None,
        "program": data.program,
    }
