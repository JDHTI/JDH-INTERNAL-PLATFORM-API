"""Use cases for reading the shared master catalog."""

from __future__ import annotations

from app.core.errors import InvalidInputError, NotFoundError
from app.modules.masters.domain.entities import (
    MasterDetail,
    MasterRecordPage,
    MasterSummary,
    clean_master_slug,
)
from app.modules.masters.domain.ports import MasterRepository

MAX_PAGE_SIZE = 200


class MasterService:
    """Coordinate read access to masters."""

    def __init__(self, repository: MasterRepository) -> None:
        """Create the service.

        Args:
            repository (MasterRepository): Master persistence port.
        """

        self.repository = repository

    async def list_masters(self) -> list[MasterSummary]:
        """List active masters ordered by sort order and name."""

        return await self.repository.list_masters()

    async def get_master(self, slug: str) -> MasterDetail:
        """Return one master with its fields or raise not found."""

        detail = await self.repository.get_master(clean_master_slug(slug))
        if detail is None:
            raise NotFoundError("Maestro no encontrado")
        return detail

    async def list_records(
        self,
        slug: str,
        query: str | None,
        is_active: bool | None,
        offset: int,
        limit: int,
    ) -> MasterRecordPage:
        """Search a master's records by code or name."""

        if offset < 0:
            raise InvalidInputError("El desplazamiento no puede ser negativo")
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"El límite debe estar entre 1 y {MAX_PAGE_SIZE}")
        term = " ".join(query.split()) if query else None
        page = await self.repository.list_records(
            clean_master_slug(slug), term or None, is_active, offset, limit
        )
        if page is None:
            raise NotFoundError("Maestro no encontrado")
        return page
