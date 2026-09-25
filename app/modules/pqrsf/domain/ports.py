"""Persistence contracts for the PQRSF module."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.modules.pqrsf.domain.entities import (
    PqrsfAttachment,
    PqrsfFilters,
    PqrsfPage,
    PqrsfRequest,
    RequestType,
    SanitizedImage,
)


class PqrsfRepository(Protocol):
    """Store classifications and requests."""

    async def list_request_types(self, active_only: bool = True) -> list[RequestType]:
        """List request types ordered by display name."""
        ...

    async def get_request_type(self, request_type_id: UUID) -> RequestType | None:
        """Find one request type by UUID."""
        ...

    async def get_by_idempotency_key(self, key: str) -> PqrsfRequest | None:
        """Find a request previously submitted with a key."""
        ...

    async def create_request(self, request: PqrsfRequest) -> PqrsfRequest:
        """Persist a request."""
        ...

    async def get_request(self, request_id: UUID) -> PqrsfRequest | None:
        """Find one request with its type and site."""
        ...

    async def list_requests(self, filters: PqrsfFilters, page: int, size: int) -> PqrsfPage:
        """List administrator results."""
        ...

    async def add_attachments(self, attachments: list[PqrsfAttachment]) -> None:
        """Persist sanitized attachment metadata."""
        ...

    async def list_attachments(self, request_id: UUID) -> list[PqrsfAttachment]:
        """List a request's attachments."""
        ...

    async def get_attachment(self, request_id: UUID, attachment_id: UUID) -> PqrsfAttachment | None:
        """Find one attachment owned by a request."""
        ...

    async def delete_request(self, request_id: UUID) -> None:
        """Delete a request and its metadata after a storage rollback."""
        ...


class ImageProcessor(Protocol):
    """Validate and sanitize an uploaded image."""

    async def sanitize(
        self, filename: str | None, content_type: str | None, content: bytes
    ) -> SanitizedImage:
        """Return safe bytes or raise an application validation error."""
        ...


class MalwareScanner(Protocol):
    """Scan sanitized bytes with an antivirus service."""

    async def scan(self, content: bytes) -> None:
        """Return when clean or raise when the scanner rejects the content."""
        ...


class BinaryStorage(Protocol):
    """Private object storage needed by PQRSF attachments."""

    async def put_object(self, key: str, body: bytes, content_type: str | None = None) -> None:
        """Store bytes under a generated key."""
        ...

    async def get_object(self, key: str) -> bytes:
        """Read bytes from a private key."""
        ...

    async def delete_object(self, key: str) -> None:
        """Delete an object during cleanup."""
        ...
