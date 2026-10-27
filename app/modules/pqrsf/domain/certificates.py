"""Certificates sent automatically, and the log of the requests answered with them."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from app.modules.pqrsf.domain.entities import RequestedDocument
from app.modules.sites.domain.entities import Site

# The category of the public form whose documents always need a person.
MANUAL_CATEGORY = "otro"

# Where a delivery stands, from the emails queued for it.
DELIVERY_PENDING = "pending"
DELIVERY_SENT = "sent"
DELIVERY_FAILED = "failed"

# Why a request that could have been answered automatically opened a case instead.
FALLBACK_DISABLED = "disabled"
FALLBACK_RECIPIENT_LIMIT = "recipient_limit"


@dataclass(frozen=True)
class ManagerDocument:
    """A file of the quality document manager, with where it is kept."""

    id: UUID
    name: str
    filename: str
    content_type: str
    size_bytes: int
    folder_id: UUID
    folder_name: str
    area_slug: str
    area_name: str

    @property
    def path(self) -> str:
        """Area, folder and document, as the manager shows them."""

        return f"{self.area_name} / {self.folder_name} / {self.name}"


@dataclass
class Certificate:
    """The file a document type is answered with."""

    id: UUID
    document_type_id: UUID
    document: ManagerDocument | None
    is_active: bool
    updated_at: datetime
    updated_by: UUID | None = None

    @property
    def automatic(self) -> bool:
        """Whether a request for the type is answered without a person.

        A certificate whose file was removed from the manager is not sent.
        """

        return self.is_active and self.document is not None


@dataclass(frozen=True)
class CertificateEntry:
    """A document type of the public form with the certificate configured for it, if any."""

    document_type_id: UUID
    name: str
    category_code: str | None
    category_name: str | None
    certificate: Certificate | None = None


@dataclass(frozen=True)
class DeliveryItem:
    """One document sent to a requester."""

    document_type_id: UUID | None
    name: str
    document_id: UUID | None
    filename: str
    size_bytes: int

    def to_json(self) -> dict:
        """The form kept in the delivery row."""

        return {
            "document_type_id": str(self.document_type_id) if self.document_type_id else None,
            "name": self.name,
            "document_id": str(self.document_id) if self.document_id else None,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_json(cls, value: dict) -> DeliveryItem:
        """Read the stored form."""

        return cls(
            document_type_id=UUID(value["document_type_id"])
            if value.get("document_type_id")
            else None,
            name=value["name"],
            document_id=UUID(value["document_id"]) if value.get("document_id") else None,
            filename=value.get("filename", ""),
            size_bytes=int(value.get("size_bytes", 0)),
        )


@dataclass
class DocumentDelivery:
    """A public request answered with certificates, and where its emails stand."""

    id: UUID
    full_name: str
    email: str
    created_at: datetime
    items: list[DeliveryItem]
    request_id: UUID | None = None
    site: Site | None = None
    business_name: str | None = None
    nit: str | None = None
    # What the same request asked for that goes through a case, and that case.
    manual_items: list[str] = field(default_factory=list)
    case_code: str | None = None
    notification_ids: list[UUID] = field(default_factory=list)
    read_at: datetime | None = None
    read_by_user_id: UUID | None = None
    # Derived from the emails when the delivery is read.
    status: str = DELIVERY_PENDING
    sent_at: datetime | None = None
    error: str | None = None


@dataclass(frozen=True)
class DeliveryFilters:
    """What the list of deliveries can be narrowed by."""

    query: str | None = None
    unread_only: bool = False
    created_from: date | None = None
    created_to: date | None = None


@dataclass(frozen=True)
class DeliveryPage:
    """One page of deliveries."""

    items: list[DocumentDelivery]
    total: int
    page: int
    size: int


@dataclass(frozen=True)
class DeliverySummary:
    """Totals for the menu badge."""

    total: int
    unread: int


@dataclass(frozen=True)
class DeliveryPlan:
    """How a document request splits: what is sent now and what a person answers."""

    automatic: list[DeliveryItem]
    manual: list[RequestedDocument]
    fallback: str | None = None
