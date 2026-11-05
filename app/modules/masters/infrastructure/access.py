"""Who reads which master: the areas where ``maestros`` is on and the person's role there."""

from __future__ import annotations

from app.modules.areas.application.service import AreaService
from app.modules.areas.domain.catalog import MANAGE, MASTERS_MODULE
from app.modules.identity.domain.principal import Principal


class AreaMasterAccess:
    """Answer :class:`MasterAccess` from the area module settings and role grants.

    An area with ``maestros`` on and no ``masters`` list opens every master; a list
    narrows that area to those masters. A person reads the union over their areas.
    """

    def __init__(self, areas: AreaService) -> None:
        """Create the adapter.

        Args:
            areas (AreaService): Area access use cases.
        """

        self.areas = areas

    async def visible_masters(self, principal: Principal) -> frozenset[str] | None:
        """Master slugs the person may read; ``None`` is every master."""

        configs = await self.areas.module_configs(principal, MASTERS_MODULE)
        if configs is None:
            return None
        visible: set[str] = set()
        for config in configs.values():
            masters = config.get("masters")
            if masters is None:
                return None
            visible.update(masters)
        return frozenset(visible)

    async def area_masters(self, slug: str) -> frozenset[str] | None:
        """Masters switched on in one area; ``None`` is every master, empty when off."""

        config = await self.areas.area_module_config(slug, MASTERS_MODULE)
        if config is None:
            return frozenset()
        masters = config.get("masters")
        return None if masters is None else frozenset(masters)

    async def can_see_private(self, principal: Principal) -> bool:
        """Personal data goes to administrators and to whoever manages ``maestros``."""

        if principal.is_platform_admin:
            return True
        return await self.areas.allows(principal, None, MASTERS_MODULE, MANAGE)
