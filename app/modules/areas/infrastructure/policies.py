"""Manager policies built on area membership."""

from __future__ import annotations

from uuid import UUID

from app.core.errors import ForbiddenError, NotFoundError
from app.modules.areas.application.service import AreaService
from app.modules.areas.domain.catalog import MANAGE, READ, is_enabled
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


class AnyPermissionPolicy:
    """Several module permissions where any one is enough: the price increase history is
    managed by BI's masters (``maestros``) and by Abastecimiento's ``acumulado-alzas``."""

    def __init__(self, *policies: PermissionPolicy) -> None:
        """Create the policy.

        Args:
            *policies (PermissionPolicy): The policies; holding one is enough.
        """

        if not policies:
            raise ValueError("AnyPermissionPolicy needs at least one policy")
        self.policies = policies

    async def is_manager(self, principal: Principal) -> bool:
        """Whether the caller manages any of the modules."""

        for policy in self.policies:
            if await policy.is_manager(principal):
                return True
        return False

    async def can_read(self, principal: Principal) -> bool:
        """Whether the caller reads any of the modules."""

        for policy in self.policies:
            if await policy.can_read(principal):
                return True
        return False

    async def require(self, principal: Principal) -> None:
        """Raise unless the caller manages one of the modules.

        Raises:
            ForbiddenError: When not allowed.
        """

        if not await self.is_manager(principal):
            raise ForbiddenError("No tienes permiso para gestionar este módulo")


class AreaLeaderPolicy:
    """Only the leader of one area (its leader role or its responsible) and the platform
    administrators (owner, admin): for what no module grant gives, like deleting records for
    good (2026-11-23). A moderator never passes."""

    def __init__(self, areas: AreaService, area_slug: str | None) -> None:
        """Create the policy.

        Args:
            areas (AreaService): Area access use cases.
            area_slug (str | None): The area whose leader may; blank leaves only the platform
                administrators.
        """

        self.areas = areas
        self.area_slug = (area_slug or "").strip() or None

    async def is_manager(self, principal: Principal) -> bool:
        """Whether the caller leads the area or administers the platform."""

        if principal.is_platform_admin:
            return True
        if self.area_slug is None or principal.is_moderator:
            return False
        grants = (await self.areas.profile(principal)).areas.get(self.area_slug)
        return grants is not None and (grants.is_leader or grants.is_responsible)


class ExplicitGrantPolicy:
    """``manage`` on one module of one area granted by name, never through the wildcard ``*``
    (2026-11-23): for a key that widens what a person sees, like ``comercial-director``, which
    a Comercial role holding ``*`` must not get by accident. The area's leader or responsible
    and the platform administrators pass; a moderator never does."""

    def __init__(self, areas: AreaService, area_slug: str | None, module: str) -> None:
        """Create the policy.

        Args:
            areas (AreaService): Area access use cases.
            area_slug (str | None): The area whose roles grant the module; blank leaves only
                the platform administrators.
            module (str): Module key of the catalog.
        """

        self.areas = areas
        self.area_slug = (area_slug or "").strip() or None
        self.module = module

    async def is_manager(self, principal: Principal) -> bool:
        """Whether the caller holds ``manage`` on the module by name in the area."""

        if principal.is_platform_admin:
            return True
        if self.area_slug is None or principal.is_moderator:
            return False
        profile = await self.areas.profile(principal)
        grants = profile.areas.get(self.area_slug)
        if grants is None or not is_enabled(self.area_slug, self.module, profile.overrides):
            return False
        return grants.is_leader or grants.is_responsible or grants.grants.get(self.module) == MANAGE


class AreaTeam:
    """The people of one area as its leader sees them (2026-11-23): the call center's leader
    sees the quotations of every member of the area; each member, only its own."""

    def __init__(
        self, areas: AreaService, repository: AreaRepository, area_slug: str | None
    ) -> None:
        """Create it.

        Args:
            areas (AreaService): Area access use cases (the caller's profile).
            repository (AreaRepository): Where areas and members are read.
            area_slug (str | None): The area; blank means no team.
        """

        self.areas = areas
        self.repository = repository
        self.area_slug = (area_slug or "").strip() or None

    async def led_members(self, principal: Principal) -> frozenset[UUID] | None:
        """The users of the area when the caller leads it (its leader role or its
        responsible); ``None`` otherwise."""

        if self.area_slug is None:
            return None
        grants = (await self.areas.profile(principal)).areas.get(self.area_slug)
        if grants is None or not (grants.is_leader or grants.is_responsible):
            return None
        area = await self.repository.get_by_slug(self.area_slug)
        if area is None:
            return None
        return frozenset(member.user_id for member in await self.repository.list_members(area.id))
