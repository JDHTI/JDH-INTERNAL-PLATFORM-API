"""Special cases: particular client situations that need manual configuration.

A case works as a request. It tracks what is being done for that client and what business
logic still has to be programmed in code (``rule_to_program``), so the workbook's
one-off agreements (Summar, Sodexo CEDI...) do not hide in notes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class SpecialCaseData:
    """What a person fills in for a case."""

    title: str
    request: str
    commercial_supplier_id: UUID | None = None
    customer_nit: str | None = None
    customer_name: str | None = None
    rule_to_program: str | None = None
    status: str = "pendiente"
    priority: str = "media"
    assignee: str | None = None
    resolution: str | None = None
    source_key: str | None = None


@dataclass(frozen=True)
class SpecialCase:
    """A stored case."""

    id: UUID
    code: str
    data: SpecialCaseData
    created_by: str
    updated_by: str
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SpecialCaseView:
    """A case with the supplier's name and the supports linked to it."""

    case: SpecialCase
    supplier_code: str | None
    supplier_name: str | None
    support_count: int


@dataclass(frozen=True)
class SpecialCasePage:
    """A page of cases."""

    items: list[SpecialCaseView]
    total: int
    page: int
    size: int
