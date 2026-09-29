"""Use cases for managing the PQRSF site catalog."""

from __future__ import annotations

from uuid import UUID

from app.core.errors import ConflictError, NotFoundError
from app.modules.sites.domain.entities import (
    Site,
    clean_site_abbreviation,
    clean_site_code,
    clean_site_name,
)
from app.modules.sites.domain.ports import SiteRepository


class SiteService:
    """Validate and coordinate site catalog operations."""

    def __init__(self, repository: SiteRepository) -> None:
        """Create the service.

        Args:
            repository (SiteRepository): Site persistence port.
        """

        self.repository = repository

    async def list_sites(self, include_inactive: bool = False) -> list[Site]:
        """List sites for administration or the public catalog.

        Args:
            include_inactive (bool): Include disabled sites for administrators.

        Returns:
            list[Site]: Sites ordered by name.
        """

        return await self.repository.list_sites(include_inactive)

    async def get_site(self, site_id: UUID) -> Site:
        """Return one site or raise not found."""

        site = await self.repository.get_by_id(site_id)
        if site is None:
            raise NotFoundError("Sede no encontrada")
        return site

    async def create_site(
        self, code: str, name: str, abbreviation: str, is_active: bool = True
    ) -> Site:
        """Create a site with a stable code and its consecutive letters."""

        code = clean_site_code(code)
        name = clean_site_name(name)
        abbreviation = clean_site_abbreviation(abbreviation)
        if await self.repository.get_by_code(code):
            raise ConflictError("Ya existe una sede con ese código")
        return await self.repository.create_site(code, name, abbreviation, is_active)

    async def update_site(
        self,
        site_id: UUID,
        name: str | None,
        is_active: bool | None,
        fields: frozenset[str],
        abbreviation: str | None = None,
    ) -> Site:
        """Update a site's display fields while keeping its code immutable.

        A new abbreviation applies to the next PQRSF consecutives of the site;
        existing cases keep the code they were given.
        """

        site = await self.get_site(site_id)
        if "name" in fields and name is not None:
            site.name = clean_site_name(name)
        if "abbreviation" in fields and abbreviation is not None:
            site.abbreviation = clean_site_abbreviation(abbreviation)
        if "is_active" in fields and is_active is not None:
            site.is_active = is_active
        return await self.repository.save_site(site)

    async def delete_site(self, site_id: UUID) -> None:
        """Delete a site and translate references into a conflict."""

        await self.get_site(site_id)
        try:
            await self.repository.delete_site(site_id)
        except ConflictError:
            raise ConflictError(
                "La sede tiene solicitudes PQRSF asociadas; desactívala en lugar de eliminarla"
            ) from None
