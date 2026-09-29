"""Persistence contract for the site catalog."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.modules.sites.domain.entities import Site


class SiteRepository(Protocol):
    """Store and retrieve PQRSF sites."""

    async def list_sites(self, include_inactive: bool = False) -> list[Site]:
        """Return sites ordered by display name."""
        ...

    async def get_by_id(self, site_id: UUID) -> Site | None:
        """Return one site by identifier."""
        ...

    async def get_by_code(self, code: str) -> Site | None:
        """Return one site by its stable code."""
        ...

    async def create_site(self, code: str, name: str, abbreviation: str, is_active: bool) -> Site:
        """Persist a site."""
        ...

    async def save_site(self, site: Site) -> Site:
        """Persist changes to a site."""
        ...

    async def delete_site(self, site_id: UUID) -> None:
        """Delete a site when no PQRSF references it."""
        ...
