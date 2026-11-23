"""Deleting supports and requests for good (2026-11-23).

Only the leader of the supports' area (BI: its leader role or its responsible) and the
platform administrators (owner, admin) delete: a bulk of supports, and support requests (JDH
and internal). Everyone else voids or expires a support, and rejects a request.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from app.core.errors import ForbiddenError, InvalidInputError
from app.modules.identity.domain.principal import Principal
from app.modules.supplier_supports.domain.ports import ManagerPolicy

MAX_DELETE = 2000
MAX_REQUESTS = 500


class DeletionRepository(Protocol):
    async def delete_supports(
        self, chosen: Mapping[UUID, int | None], reason: str, actor: str, now: datetime
    ) -> int: ...

    async def delete_requests(self, request_ids: Sequence[UUID]) -> int: ...

    async def delete_internal_requests(self, request_ids: Sequence[UUID]) -> int: ...


class SupportSearch(Protocol):
    """The master's search, to delete every support of a filter."""

    async def search_ids(self, limit: int, **criteria: Any) -> list[UUID]: ...


class SupportDeletionService:
    """Delete supports and requests: the area leader and the platform administrators."""

    def __init__(
        self, repository: DeletionRepository, deleters: ManagerPolicy, supports: SupportSearch
    ) -> None:
        """Create the service.

        Args:
            repository (DeletionRepository): Where the deletions run.
            deleters (ManagerPolicy): The area leader and the platform administrators.
            supports (SupportSearch): The master's search (a filter's supports).
        """

        self.repository = repository
        self.deleters = deleters
        self.supports = supports

    async def can_delete(self, principal: Principal) -> bool:
        """Whether the caller deletes supports and requests."""

        return await self.deleters.is_manager(principal)

    async def _require(self, principal: Principal) -> None:
        if not await self.can_delete(principal):
            raise ForbiddenError(
                "Solo el líder del área y los administradores de la plataforma eliminan "
                "apoyos y solicitudes"
            )

    async def delete_supports(
        self,
        principal: Principal,
        items: Mapping[UUID, int | None],
        criteria: Mapping[str, Any] | None,
        reason: str,
    ) -> int:
        """Delete a selection of supports for good: the ones listed (with the version seen)
        plus every one of a search filter, at most :data:`MAX_DELETE`, all or none.

        Raises:
            ForbiddenError: The caller does not lead the area nor administers the platform.
            InvalidInputError: No reason, or a selection empty or too large.
        """

        await self._require(principal)
        text = " ".join(reason.split()) if reason else ""
        if not text:
            raise InvalidInputError("Indica por qué se eliminan")
        chosen = dict(items)
        if criteria is not None:
            found = await self.supports.search_ids(
                MAX_DELETE + 1, **{**criteria, "include_voided": True}
            )
            chosen.update((support_id, None) for support_id in found if support_id not in chosen)
        if not 1 <= len(chosen) <= MAX_DELETE:
            raise InvalidInputError(f"Selecciona entre 1 y {MAX_DELETE} apoyos")
        return await self.repository.delete_supports(
            chosen, text, principal.email, datetime.now(UTC)
        )

    @staticmethod
    def _ids(request_ids: Sequence[UUID]) -> list[UUID]:
        ids = list(dict.fromkeys(request_ids))
        if not 1 <= len(ids) <= MAX_REQUESTS:
            raise InvalidInputError(f"Selecciona entre 1 y {MAX_REQUESTS} solicitudes")
        return ids

    async def delete_requests(self, principal: Principal, request_ids: Sequence[UUID]) -> int:
        """Delete JDH support requests (pending or decided), all or none.

        Raises:
            ForbiddenError: The caller does not lead the area nor administers the platform.
        """

        await self._require(principal)
        return await self.repository.delete_requests(self._ids(request_ids))

    async def delete_internal_requests(
        self, principal: Principal, request_ids: Sequence[UUID]
    ) -> int:
        """Delete internal support requests (pending or decided), all or none.

        Raises:
            ForbiddenError: The caller does not lead the area nor administers the platform.
        """

        await self._require(principal)
        return await self.repository.delete_internal_requests(self._ids(request_ids))
