"""PQRSF domain entities and input normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from app.core.errors import InvalidInputError
from app.modules.sites.domain.entities import Site

HANDLING_CASE = "case"
HANDLING_ACKNOWLEDGE = "acknowledge"
REQUEST_TYPE_CODES = frozenset({"peticion", "queja", "reclamo", "sugerencia", "felicitacion"})
# The description of a type is one visible line under the option, never a paragraph.
MAX_TYPE_DESCRIPTION = 120
# How a cause is treated: escalated to the manufacturer, a process failure of JDH, or neither.
CAUSE_ROUTES = frozenset({"supplier", "internal", "none"})
_IDEMPOTENCY_KEY_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


@dataclass
class RequestType:
    """A fixed PQRSF classification and its input capabilities."""

    id: UUID
    code: str
    name: str
    allows_document_request: bool
    allows_attachments: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # Follow-up settings: consecutive prefix (the P of P26AC0001), whether cases of
    # this type are tracked against a due date, and that default due time.
    prefix: str = ""
    requires_follow_up: bool = True
    sla_days: int | None = None
    # What a request of this type becomes: a follow-up case, or a stored message that is
    # thanked at once (suggestions and compliments).
    handling: str = HANDLING_CASE
    # One short line shown under the option so the requester knows which type to pick.
    description: str | None = None
    # Whether the form asks the requester for the area (every type but compliments).
    asks_area: bool = False


@dataclass(frozen=True)
class AreaRef:
    """An area (dependency) as the requester picked it."""

    id: UUID
    code: str
    name: str


@dataclass(frozen=True)
class Cause:
    """The cause of a subject: it decides who attends the case and how it is treated."""

    id: UUID
    subject_id: UUID
    code: str
    name: str
    description: str | None
    dependency_id: UUID | None
    # ``supplier`` may be escalated to the manufacturer; ``internal`` is a process failure.
    route: str
    requires_product: bool
    allows_product: bool
    is_other: bool
    sort_order: int = 0
    is_active: bool = True


@dataclass(frozen=True)
class Subject:
    """What a request is about, offered to some request types, with its causes."""

    id: UUID
    code: str
    name: str
    description: str | None
    request_type_codes: tuple[str, ...]
    dependency_id: UUID | None
    is_other: bool
    causes: tuple[Cause, ...] = ()
    sort_order: int = 0
    is_active: bool = True

    def offered_to(self, request_type_code: str) -> bool:
        """Whether the form offers it for a request type."""

        return request_type_code in self.request_type_codes


@dataclass(frozen=True)
class ProductInput:
    """A product line as the requester typed it."""

    invoice_number: str
    sku: str
    lot: str
    expiry_date: date
    affected_quantity: int


@dataclass(frozen=True)
class ClaimedProduct:
    """A product line of a complaint or claim, with the master data seen at submission."""

    invoice_number: str
    sku: str
    lot: str
    expiry_date: date
    affected_quantity: int
    description: str | None = None
    supplier_code: str | None = None
    supplier_name: str | None = None


@dataclass(frozen=True)
class ProductOption:
    """A product the public form offers when the requester types a reference."""

    sku: str
    description: str | None


@dataclass(frozen=True)
class ProductMaster:
    """What the product master knows of a reference: its name and its supplier."""

    sku: str
    description: str | None
    supplier_code: str | None
    supplier_name: str | None


@dataclass
class PqrsfRequest:
    """A submitted PQRSF request."""

    id: UUID
    reference: str
    request_type: RequestType
    site: Site
    full_name: str
    business_name: str
    nit: str
    address: str
    department: str
    city: str
    email: str
    subject: str | None
    description: str | None
    idempotency_key: str
    idempotency_hash: str
    created_at: datetime
    updated_at: datetime
    attachments: list[PqrsfAttachment] = field(default_factory=list)
    # A petition for documents: the category picked and the documents asked for.
    document_request: bool = False
    document_category_id: UUID | None = None
    documents: list[RequestedDocument] = field(default_factory=list)
    # Dependency of that category, read when the request is loaded.
    document_dependency_id: UUID | None = None
    # Classification picked from the catalogs, read when the request is loaded.
    subject_ref: Subject | None = None
    cause_ref: Cause | None = None
    products: list[ClaimedProduct] = field(default_factory=list)
    # ``yes`` | ``no`` | ``unknown``, asked only when there are products.
    pickup_order_requested: str | None = None
    pickup_order_number: str | None = None
    # Area the requester picked on the form.
    area: AreaRef | None = None
    # When the order with the products was delivered; required with products.
    delivery_date: date | None = None

    @property
    def dependency_id(self) -> UUID | None:
        """The dependency that attends it.

        The cause's, else the subject's, else the document category's (Quality set it for
        those documents), else the area the requester picked.
        """

        if self.cause_ref is not None and self.cause_ref.dependency_id is not None:
            return self.cause_ref.dependency_id
        if self.subject_ref is not None and self.subject_ref.dependency_id is not None:
            return self.subject_ref.dependency_id
        if self.document_dependency_id is not None:
            return self.document_dependency_id
        return self.area.id if self.area is not None else None

    @property
    def case_subject(self) -> str | None:
        """One line for the case: the subject and cause picked, or the typed subject.

        With "Otro" the typed subject (when there is one) says more than the word "Otro".
        """

        if self.subject_ref is None:
            return self.subject
        parts = [self.subject_ref.name]
        if self.cause_ref is not None:
            parts.append(self.cause_ref.name)
        line = " · ".join(parts)
        if self.subject and (
            self.subject_ref.is_other or (self.cause_ref and self.cause_ref.is_other)
        ):
            line = f"{line}: {self.subject}"
        return line[:200]


PICKUP_ORDER_ANSWERS = frozenset({"yes", "no", "unknown"})


@dataclass(frozen=True)
class RequestedDocument:
    """One document a customer asks for: a catalog type, or free text for anything else."""

    document_type_id: UUID | None
    description: str | None = None


@dataclass(frozen=True)
class DocumentOption:
    """A catalog document offered on the public form."""

    id: UUID
    name: str
    # A certificate answers it automatically: no case and no waiting.
    automatic: bool = False


@dataclass(frozen=True)
class DocumentCategory:
    """A group of documents offered together, and who attends requests for them."""

    id: UUID
    code: str
    name: str
    dependency_id: UUID | None
    documents: list[DocumentOption]


@dataclass
class PqrsfAttachment:
    """Sanitized image metadata stored next to a PQRSF request."""

    id: UUID
    request_id: UUID
    original_filename: str
    extension: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    storage_key: str
    created_at: datetime


@dataclass(frozen=True)
class SanitizedImage:
    """Image bytes after format validation and metadata stripping."""

    original_filename: str
    extension: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class ImageUpload:
    """Raw multipart image passed from the HTTP boundary."""

    filename: str | None
    content_type: str | None
    content: bytes


@dataclass(frozen=True)
class PqrsfFilters:
    """Optional administrator filters."""

    request_type_id: UUID | None = None
    site_id: UUID | None = None
    reference: str | None = None


@dataclass(frozen=True)
class PqrsfPage:
    """One page of administrator results."""

    items: list[PqrsfRequest]
    total: int
    page: int
    size: int


@dataclass(frozen=True)
class CreateResult:
    """New or replayed request returned by the idempotent create use case."""

    request: PqrsfRequest
    replayed: bool
    # Consecutive of the follow-up case (P26AC0012) when it could be opened.
    tracking_code: str | None = None
    # ``feedback`` for a suggestion or compliment: stored apart, no case, no consecutive.
    handled_as: str = "case"
    # Names of the certificates sent to the requester without a case.
    delivered: list[str] = field(default_factory=list)


def clean_required(value: str, field: str, max_length: int) -> str:
    """Collapse whitespace and validate a required text value."""

    cleaned = " ".join(value.split())
    if not cleaned:
        raise InvalidInputError(f"El campo {field} no puede estar vacío")
    if len(cleaned) > max_length:
        raise InvalidInputError(f"El campo {field} admite máximo {max_length} caracteres")
    return cleaned


def clean_multiline(value: str | None, field: str, max_length: int) -> str | None:
    """Normalize free text that keeps its lines: a comment, a reply.

    Spaces collapse inside each line, line breaks stay, and a run of blank lines becomes
    one. Returns ``None`` when nothing is left.
    """

    if value is None:
        return None
    lines = [" ".join(line.split()) for line in value.replace("\r\n", "\n").split("\n")]
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if not cleaned:
        return None
    if len(cleaned) > max_length:
        raise InvalidInputError(f"El campo {field} admite máximo {max_length} caracteres")
    return cleaned


def clean_optional(value: str | None, field: str, max_length: int) -> str | None:
    """Normalize an optional text value and return ``None`` when blank."""

    if value is None or not value.strip():
        return None
    return clean_required(value, field, max_length)


def validate_idempotency_key(value: str) -> str:
    """Require a UUID-shaped idempotency key from public clients."""

    key = value.strip().lower()
    if not _IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
        raise InvalidInputError("Idempotency-Key debe ser un UUID válido")
    return key


def storage_key_for(request_id: UUID, attachment_id: UUID, extension: str) -> str:
    """Build a private object key from generated identifiers only."""

    return f"pqrsf/{request_id}/{attachment_id}.{extension}"
