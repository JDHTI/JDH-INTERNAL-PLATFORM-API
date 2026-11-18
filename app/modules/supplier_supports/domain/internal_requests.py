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
MAX_ITEMS = 200


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
    # must be lower (BI, 2026-11-18).
    target_margin_pct: Decimal | None = None


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
    "MAX_ITEMS",
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
