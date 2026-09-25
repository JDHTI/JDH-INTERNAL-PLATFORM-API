"""PQRSF domain entities and input normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass
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
    allows_reason: bool
    allows_attachments: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


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
    reason: str | None
    description: str
    idempotency_key: str
    idempotency_hash: str
    created_at: datetime
    updated_at: datetime


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
