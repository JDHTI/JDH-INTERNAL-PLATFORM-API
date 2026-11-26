"""Who closes and reopens a month of the budget: the leader of any of several areas."""

from __future__ import annotations

from collections.abc import Sequence

from app.modules.areas.application.service import AreaService
from app.modules.areas.infrastructure.policies import AreaLeaderPolicy
from app.modules.identity.domain.principal import Principal


class AnyAreaLeaderPolicy:
    """The leader or responsible of any of the areas (BI and Comercial), and the platform
    administrators; a moderator never passes."""

    def __init__(self, areas: AreaService, area_slugs: Sequence[str]) -> None:
        slugs = [slug for slug in area_slugs if slug and slug.strip()]
        self.policies = [AreaLeaderPolicy(areas, slug) for slug in slugs] or [
            AreaLeaderPolicy(areas, None)
        ]

    async def is_manager(self, principal: Principal) -> bool:
        for policy in self.policies:
            if await policy.is_manager(principal):
                return True
        return False


__all__ = ["AnyAreaLeaderPolicy"]
