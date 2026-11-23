"""Internal support requests (authorized margin): a supported margin for a customer.

Whoever quotes asks, for the quotation's customer, a supported margin on one or more SKUs of
one supplier; or it is asked from the requests panel by customer and SKU (no quotation). The
margin asked must be lower than the product's optimal margin on the customer's list (BI,
2026-11-18). BI (or a designated reviewer) decides:

- ``persistente``: each approved SKU becomes a support with the reviewer's validity;
- ``unica_vez``: the same support valid only on the order's date (``requested_for``);
- or a rejection.

The support is ``excepcion_margen`` / ``margen_minimo`` with ``min_margin_pct`` and
``supported_margin_pct`` set to the approved margin, which the order validator already applies
(customer x SKU x supplier, in force on the order's date). It is published to the DWH like any
other change of the master, and a bulk load never closes it.

A **supplier margin request** (``kind = proveedor``, 2026-11-23) is asked from a quotation line
covered by a % Margen Garantizado with the supplier's minimum margin (13 %, say): a lower
minimum (7 %, 0 % or negative) for that quotation's customer. Approving copies the guaranteed
support for the customer and SKU with the approved minimum (once, or with a validity); the
base support keeps its minimum for everyone else. The quoter then picks the copy, which pays
more. It is the supplier's exception, not a margin JDH gives up.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.modules.identity.domain.principal import Principal
from app.modules.supplier_supports.domain.entities import SupportData

PENDING, APPROVED, REJECTED = "pendiente", "aprobada", "rechazada"
FOR_GOOD, ONCE = "persistente", "unica_vez"
JDH_KIND, SUPPLIER_KIND = "jdh", "proveedor"
KINDS = (JDH_KIND, SUPPLIER_KIND)
MAX_ITEMS = 200


def kind_label(kind: str, supplier_name: str | None, supplier_code: str | None) -> str:
    """How the requests module names a request."""

    if kind == SUPPLIER_KIND:
        return f"Solicitud de margen proveedor {supplier_name or supplier_code or ''}".strip()
    return "Excepción margen mínimo autorizado JDH"


@dataclass(frozen=True)
class RequestItem:
    """One SKU of a request: the quotation line as it was, the margin asked and the decision."""

    sku: str
    requested_margin_pct: Decimal
    description: str | None = None
    unit: str | None = None
    cost: Decimal | None = None
    quoted_price: Decimal | None = None
    # Margin the quoted price leaves (with the supports in force).
    margin_pct: Decimal | None = None
    # Minimum of the product or the rule on that line.
    min_margin_pct: Decimal | None = None
    approved_margin_pct: Decimal | None = None
    support_id: UUID | None = None
    # The product's optimal margin on the customer's list when it was asked: the margin asked
    # must be lower (BI, 2026-11-18). In a supplier request, the supplier's minimum in force.
    target_margin_pct: Decimal | None = None
    # A supplier request: the % Margen Garantizado it lowers (approving copies it).
    base_support_id: UUID | None = None


@dataclass(frozen=True)
class InternalRequest:
    """A stored request."""

    id: UUID
    number: int
    status: str
    decision: str | None
    quotation_id: UUID | None
    quotation_label: str | None
    customer_nit: str
    customer_name: str | None
    commercial_supplier_id: UUID
    supplier_code: str
    supplier_name: str | None
    requested_for: date
    agreed_with: str
    reason: str
    requested_by_id: UUID
    requested_by: str
    created_at: datetime
    version: int
    valid_from: date | None
    valid_to: date | None
    decided_by: str | None
    decided_at: datetime | None
    decision_reason: str | None
    items: tuple[RequestItem, ...]
    kind: str = JDH_KIND


@dataclass(frozen=True)
class NewRequest:
    """A request ready to be stored (checked by the service). Without a quotation it was
    asked from the requests panel, by customer and SKU."""

    quotation_id: UUID | None
    quotation_label: str | None
    customer_nit: str
    customer_name: str | None
    commercial_supplier_id: UUID
    supplier_code: str
    supplier_name: str | None
    requested_for: date
    agreed_with: str
    reason: str
    items: tuple[RequestItem, ...]
    kind: str = JDH_KIND


@dataclass(frozen=True)
class RequestRef:
    """The latest request of a SKU in a quotation, as the quotation line shows it."""

    id: UUID
    status: str
    decision: str | None
    approved_margin_pct: Decimal | None


@dataclass(frozen=True)
class QuotedLine:
    """What a request takes from a quotation line."""

    sku: str
    description: str | None
    unit: str | None
    supplier_code: str | None
    supplier_name: str | None
    cost: Decimal | None
    quoted_price: Decimal | None
    margin_pct: Decimal | None
    min_margin_pct: Decimal | None
    is_local: bool = False
    # The support applied to the line: a supplier request lowers its supplier's minimum.
    support_id: UUID | None = None
    support_calc_method: str | None = None
    supplier_min_margin_pct: Decimal | None = None


@dataclass(frozen=True)
class QuotedCustomer:
    """The quotation a request comes from, read with the caller's own permission."""

    id: UUID
    kind: str
    version: int
    label: str | None
    customer_nit: str | None
    customer_name: str | None
    lines: Mapping[str, QuotedLine]


@dataclass(frozen=True)
class Approval:
    """What the reviewer decided, already checked, with the supports to create by SKU."""

    decision: str
    valid_from: date
    valid_to: date | None
    note: str | None
    margins: Mapping[str, Decimal]
    supports: Mapping[str, SupportData]


class QuotationAccess(Protocol):
    """The quoter, as the requests see it."""

    async def quotation(self, principal: Principal, quotation_id: UUID) -> QuotedCustomer:
        """The quotation if the caller may read it (404/403 otherwise)."""
        ...

    async def can_quote(self, principal: Principal) -> bool:
        """Whether the caller uses the quoter (manages ``cotizador``)."""
        ...


class InternalRequestRepository(Protocol):
    """Storage of the requests; approving writes the supports in the same transaction."""

    async def create(
        self, request: NewRequest, actor_id: UUID, actor: str, now: datetime
    ) -> InternalRequest: ...

    async def get(self, request_id: UUID) -> InternalRequest | None: ...

    async def search(
        self,
        *,
        status: str | None,
        q: str | None,
        owner_id: UUID | None,
        page: int,
        size: int,
    ) -> tuple[list[InternalRequest], int]: ...

    async def approve(
        self, request_id: UUID, version: int, approval: Approval, actor: str, now: datetime
    ) -> InternalRequest: ...

    async def reject(
        self, request_id: UUID, version: int, reason: str, actor: str, now: datetime
    ) -> InternalRequest: ...

    async def latest_for_quotation(self, quotation_id: UUID) -> dict[str, RequestRef]: ...


__all__ = [
    "APPROVED",
    "FOR_GOOD",
    "JDH_KIND",
    "KINDS",
    "MAX_ITEMS",
    "SUPPLIER_KIND",
    "kind_label",
    "ONCE",
    "PENDING",
    "REJECTED",
    "Approval",
    "InternalRequest",
    "InternalRequestRepository",
    "NewRequest",
    "QuotationAccess",
    "QuotedCustomer",
    "QuotedLine",
    "RequestItem",
    "RequestRef",
]
