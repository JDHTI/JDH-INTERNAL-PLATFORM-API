"""Supplier support profiles: the defaults and file layout of one commercial supplier."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

# Fields a profile may pre-fill. Identity of the row (supplier, customer, SKU, dates, value)
# never comes from a default: it changes on every support.
DEFAULT_FIELDS = (
    "kind",
    "funding",
    "calc_method",
    "value_unit",
    "settlement_method",
    "settlement_frequency",
    "program",
    "regionals",
    "business_units",
    "channels",
    "warehouse",
    "brand",
    "jdh_pct",
    "target_margin_pct",
    "min_margin_pct",
    "list_margin_pct",
    "authorized_by",
    "supplier_contact",
    "sales_rep",
    "conditions",
    "end_condition",
)
MAX_HEADERS_PER_FIELD = 10


@dataclass(frozen=True)
class ProfileData:
    """What a person sets on a profile; values are already normalized and JSON-safe."""

    defaults: dict[str, Any] = field(default_factory=dict)
    column_map: dict[str, list[str]] = field(default_factory=dict)
    sheet: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class Profile:
    """A stored profile with the supplier it belongs to."""

    id: UUID
    commercial_supplier_id: UUID
    supplier_code: str
    supplier_name: str
    data: ProfileData
    created_by: str
    updated_by: str
    version: int
    created_at: datetime
    updated_at: datetime


class ProfileRepository(Protocol):
    """Persistence of profiles."""

    async def list(self) -> list[Profile]: ...

    async def get(self, supplier_id: UUID) -> Profile | None: ...

    async def create(
        self, profile_id: UUID, supplier_id: UUID, data: ProfileData, actor: str, now: datetime
    ) -> Profile: ...

    async def update(
        self, supplier_id: UUID, data: ProfileData, expected_version: int, actor: str, now: datetime
    ) -> Profile: ...

    async def delete(self, supplier_id: UUID) -> bool: ...
