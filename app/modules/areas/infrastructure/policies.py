"""Manager policies built on area membership."""

from __future__ import annotations

from uuid import UUID

from app.core.errors import ForbiddenError, NotFoundError
from app.modules.areas.application.service import AreaService
from app.modules.areas.domain.entities import Area
from app.modules.areas.domain.ports import AreaRepository
from app.modules.identity.domain.principal import Principal


class UnrestrictedAreaAccess:
    """Resolves an area without asking whether the caller belongs to it.

    For a feature that files documents in another area's space *after* its own permission check
    (for example the supports managers filing proofs in BI's document manager). It must only be
    wired behind that check: it never authorizes on its own.
    """

    def __init__(self, repository: AreaRepository) -> None:
        """Create the access.

        Args:
            repository (AreaRepository): Where areas are read.
        """

        self.repository = repository

    async def authorize(self, slug: str, principal: Principal) -> Area:
        """The area with this slug; membership is not checked.

        Raises:
            NotFoundError: If there is no such area.
        """

        area = await self.repository.get_by_slug(slug)
        if area is None:
            raise NotFoundError("Área no encontrada")
        return area

    async def authorize_by_id(self, area_id: UUID, principal: Principal) -> Area:
        """The area with this identifier; membership is not checked.

        Raises:
            NotFoundError: If there is no such area.
        """

        area = await self.repository.get_by_id(area_id)
        if area is None:
            raise NotFoundError("Área no encontrada")
        return area

    async def accessible_area_ids(self, principal: Principal) -> list[UUID] | None:
        """Every area (the searches of this access are never open to the caller)."""

        return None


class AreaManagerPolicy:
    """Platform administrators and members of one area manage a feature."""

    def __init__(self, areas: AreaService, area_slug: str | None) -> None:
        """Create the policy.

        Args:
            areas (AreaService): Area access use cases.
            area_slug (str | None): Area whose members manage the feature; blank
                leaves management to platform administrators only.
        """

        self.areas = areas
        self.area_slug = (area_slug or "").strip() or None

    async def is_manager(self, principal: Principal) -> bool:
        """Whether the caller manages the feature.

        Args:
            principal (Principal): The caller.

        Returns:
            bool: ``True`` for administrators and members of the managing area.
        """

        if principal.is_platform_admin:
            return True
        if self.area_slug is None:
            return False
        try:
            await self.areas.authorize(self.area_slug, principal)
        except (ForbiddenError, NotFoundError):
            return False
        return True
