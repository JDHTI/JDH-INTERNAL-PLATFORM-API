"""Who reads and who manages each view of Costos."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import ForbiddenError
from app.modules.costs.domain.modules import ENTRIES, INCREASES, LEDGER, NOVELTIES
from app.modules.costs.domain.ports import ModulePolicy
from app.modules.identity.domain.principal import Principal


@dataclass(frozen=True)
class ModuleAccess:
    can_read: bool
    can_manage: bool


class CostAccess:
    """The permission policy of each Costos key, asked by key."""

    def __init__(
        self,
        entries: ModulePolicy,
        novelties: ModulePolicy,
        increases: ModulePolicy,
        ledger: ModulePolicy,
    ) -> None:
        """Create the access.

        Args:
            entries (ModulePolicy): ``analisis-entradas``.
            novelties (ModulePolicy): ``novedades-compras``.
            increases (ModulePolicy): ``alzas-proveedor``.
            ledger (ModulePolicy): ``acumulado-alzas``.
        """

        self.policies: dict[str, ModulePolicy] = {
            ENTRIES: entries,
            NOVELTIES: novelties,
            INCREASES: increases,
            LEDGER: ledger,
        }

    async def permissions(self, principal: Principal) -> dict[str, ModuleAccess]:
        """``read`` and ``manage`` of every key, for the caller.

        Args:
            principal (Principal): The caller.

        Returns:
            dict[str, ModuleAccess]: Key → what the caller may do.
        """

        access: dict[str, ModuleAccess] = {}
        for key, policy in self.policies.items():
            manage = await policy.is_manager(principal)
            access[key] = ModuleAccess(
                can_read=manage or await policy.can_read(principal), can_manage=manage
            )
        return access

    async def manages(self, principal: Principal, *keys: str) -> bool:
        """Whether the caller manages any of the keys."""

        for key in keys:
            if await self.policies[key].is_manager(principal):
                return True
        return False

    async def require_manage(self, principal: Principal, *keys: str, message: str) -> None:
        """Raise unless the caller manages one of the keys.

        Raises:
            ForbiddenError: With ``message``.
        """

        if not await self.manages(principal, *keys):
            raise ForbiddenError(message)


__all__ = ["CostAccess", "ModuleAccess"]
