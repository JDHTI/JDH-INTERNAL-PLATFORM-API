"""Persistence contracts for the PQRSF module."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.modules.pqrsf.domain.entities import (
    PqrsfFilters,
    PqrsfPage,
    PqrsfRequest,
    RequestType,
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
