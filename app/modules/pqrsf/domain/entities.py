"""PQRSF domain entities and input normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.core.errors import InvalidInputError
from app.modules.sites.domain.entities import Site

REQUEST_TYPE_CODES = frozenset({"peticion", "queja", "reclamo", "sugerencia", "felicitacion"})
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
    description: str
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


def clean_required(value: str, field: str, max_length: int) -> str:
    """Collapse whitespace and validate a required text value."""

    cleaned = " ".join(value.split())
    if not cleaned:
        raise InvalidInputError(f"El campo {field} no puede estar vacío")
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
