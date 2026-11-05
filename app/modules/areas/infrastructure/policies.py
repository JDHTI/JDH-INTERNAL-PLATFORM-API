"""Manager policies built on area membership."""

from __future__ import annotations

from uuid import UUID

from app.core.errors import ForbiddenError, NotFoundError
from app.modules.areas.application.service import AreaService
from app.modules.areas.domain.catalog import MANAGE, READ
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

    async def authorize(
        self, slug: str, principal: Principal, module: str | None = None, action: str = READ
    ) -> Area:
        """The area with this slug; membership is not checked.

        Raises:
            NotFoundError: If there is no such area.
        """

        area = await self.repository.get_by_slug(slug)
        if area is None:
            raise NotFoundError("Área no encontrada")
        return area

    async def authorize_by_id(
        self, area_id: UUID, principal: Principal, module: str | None = None, action: str = READ
    ) -> Area:
        """The area with this identifier; membership is not checked.

        Raises:
            NotFoundError: If there is no such area.
        """

        area = await self.repository.get_by_id(area_id)
        if area is None:
            raise NotFoundError("Área no encontrada")
        return area

    async def accessible_area_ids(
        self, principal: Principal, module: str | None = None, action: str = READ
    ) -> list[UUID] | None:
        """Every area (the searches of this access are never open to the caller)."""

        return None


class PermissionPolicy:
    """Who may manage (and read) one module, in one area or in any area of the catalog.

    ``manage`` comes from the area role grants (or being the area responsible); owner
    and admin always pass and a moderator only reads. ``is_manager`` keeps the name
    the feature services already call.
    """

    def __init__(
        self, areas: AreaService, area_slug: str | None, module: str, action: str = MANAGE
    ) -> None:
        """Create the policy.

        Args:
            areas (AreaService): Area access use cases.
            area_slug (str | None): Area whose roles grant the module; blank means any
                area where the catalog has the module (the order validator lives in BI
                and Comercial, and each one grants it by role).
            module (str): Module key of the catalog.
            action (str): What ``is_manager`` asks for (``manage`` by default).
        """

        self.areas = areas
        self.area_slug = (area_slug or "").strip() or None
        self.module = module
        self.action = action

    async def _allows(self, principal: Principal, action: str) -> bool:
        if principal.is_platform_admin:
            return True
        return await self.areas.allows(principal, self.area_slug, self.module, action)

    async def is_manager(self, principal: Principal) -> bool:
        """Whether the caller manages the module (``action`` of the policy).

        Args:
            principal (Principal): The caller.

        Returns:
            bool: ``True`` when the caller holds the action.
        """

        return await self._allows(principal, self.action)

    async def can_read(self, principal: Principal) -> bool:
        """Whether the caller may at least read the module in the policy's area.

        Args:
            principal (Principal): The caller.

        Returns:
            bool: ``True`` when the caller holds ``read`` or ``manage``.
        """

        return await self._allows(principal, READ)

    async def require(self, principal: Principal) -> None:
        """Raise unless the caller manages the module.

        Raises:
            ForbiddenError: When not allowed.
        """

        if not await self.is_manager(principal):
            raise ForbiddenError(f"No tienes permiso para gestionar {self.module}")


# Former name: every feature manager is now a module permission.
AreaManagerPolicy = PermissionPolicy
