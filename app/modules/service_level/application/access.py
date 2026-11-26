"""Who reads and who manages each view of Nivel de servicio."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import ForbiddenError
from app.modules.identity.domain.principal import Principal
from app.modules.service_level.domain.modules import PENDING_ORDERS, SERVICE_LEVEL
from app.modules.service_level.domain.ports import ModulePolicy


@dataclass(frozen=True)
class ModuleAccess:
    can_read: bool
    can_manage: bool


class ServiceLevelAccess:
    """The permission policy of each key of the module, asked by key."""

    def __init__(self, pending: ModulePolicy, dashboard: ModulePolicy) -> None:
        """Create the access.

        Args:
            pending (ModulePolicy): ``pendientes-pedidos``.
            dashboard (ModulePolicy): ``nivel-servicio``.
        """

        self.policies: dict[str, ModulePolicy] = {PENDING_ORDERS: pending, SERVICE_LEVEL: dashboard}

    async def permissions(self, principal: Principal) -> dict[str, ModuleAccess]:
        """``read`` and ``manage`` of every key, for the caller."""

        access: dict[str, ModuleAccess] = {}
        for key, policy in self.policies.items():
            manage = await policy.is_manager(principal)
            access[key] = ModuleAccess(
                can_read=manage or await policy.can_read(principal), can_manage=manage
            )
        return access

    async def require_manage(self, principal: Principal, key: str, message: str) -> None:
        """Raise unless the caller manages the key.

        Raises:
            ForbiddenError: With ``message``.
        """

        if not await self.policies[key].is_manager(principal):
            raise ForbiddenError(message)


__all__ = ["ModuleAccess", "ServiceLevelAccess"]
