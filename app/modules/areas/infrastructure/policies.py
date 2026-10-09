"""Manager policies built on area membership."""

from __future__ import annotations

from app.core.errors import ForbiddenError, NotFoundError
from app.modules.areas.application.service import AreaService
from app.modules.identity.domain.principal import Principal


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
