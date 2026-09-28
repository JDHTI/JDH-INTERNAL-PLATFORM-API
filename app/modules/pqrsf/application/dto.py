"""Commands and results of the PQRSF follow-up use cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from app.modules.identity.domain.principal import DirectoryUser
from app.modules.pqrsf.domain.entities import PqrsfRequest, RequestType
from app.modules.pqrsf.domain.tracking import (
    CaseEvent,
    CaseStatus,
    Dependency,
    DocumentType,
    TrackingCase,
)
from app.modules.sites.domain.entities import Site


@dataclass(frozen=True)
class CaseAccess:
    """What the caller may do with one case."""

    can_view: bool
    can_work: bool
    can_assign: bool


@dataclass(frozen=True)
class CaseView:
    """A case with the people it references and the caller's permissions."""

    case: TrackingCase
    assignee: DirectoryUser | None
    created_by: DirectoryUser | None
    access: CaseAccess
    is_overdue: bool = False


@dataclass(frozen=True)
class EventView:
    """A timeline entry with its author."""

    event: CaseEvent
    actor: DirectoryUser | None


@dataclass(frozen=True)
class CaseDetail:
    """A case, its timeline and, for public submissions, the intake record."""

    view: CaseView
    events: list[EventView]
    request: PqrsfRequest | None


@dataclass(frozen=True)
class CaseViewPage:
    """One page of the case inbox."""

    items: list[CaseView]
    total: int
    page: int
    size: int


@dataclass(frozen=True)
class TrackingCatalog:
    """Everything the follow-up screens need to fill their selectors."""

    request_types: list[RequestType]
    statuses: list[CaseStatus]
    dependencies: list[Dependency]
    document_types: list[DocumentType]
    sites: list[Site]
    is_manager: bool


@dataclass(frozen=True)
class CaseChanges:
    """Header fields to update; only names in ``fields`` are applied."""

    fields: frozenset[str]
    subject: str | None = None
    description: str | None = None
    received_on: date | None = None
    site_id: UUID | None = None
    dependency_id: UUID | None = None
    customer_name: str | None = None
    customer_nit: str | None = None
    supplier_name: str | None = None
    sales_executive_name: str | None = None
    requester_name: str | None = None
    requester_email: str | None = None


@dataclass(frozen=True)
class ItemChanges:
    """Item fields to update; only names in ``fields`` are applied."""

    fields: frozenset[str]
    document_type_id: UUID | None = None
    description: str | None = None
    quantity: int | None = None
    responded_on: date | None = None
    delay_cause: str | None = None
    delay_detail: str | None = None


@dataclass(frozen=True)
class ImportedItem:
    """A historical document row as found in the spreadsheet."""

    document_type_id: UUID | None
    description: str | None
    quantity: int | None
    sla_days: int | None
    responded_on: date | None
    delay_cause: str | None = None
    delay_detail: str | None = None


@dataclass(frozen=True)
class ImportedCase:
    """A historical case assembled from consecutive spreadsheet rows."""

    source_key: str
    request_type_id: UUID
    number: int | None
    received_on: date
    status_code: str
    assignee_user_id: UUID | None = None
    dependency_id: UUID | None = None
    site_id: UUID | None = None
    customer_name: str | None = None
    supplier_name: str | None = None
    sales_executive_name: str | None = None
    requester_name: str | None = None
    items: tuple[ImportedItem, ...] = ()
    legacy: dict = field(default_factory=dict)
