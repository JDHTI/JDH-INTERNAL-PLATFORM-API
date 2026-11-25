"""What the cost settings need from the outside."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.modules.cost_settings.domain.entities import BranchRule, Change
from app.modules.identity.domain.principal import Principal


class CostSettingsRepository(Protocol):
    async def parameters(self) -> dict[str, tuple[Decimal, str | None, datetime | None]]:
        """Stored values by key: ``(value, updated_by, updated_at)``."""
        ...

    async def set_parameters(
        self, values: Mapping[str, Decimal], actor: str, at: datetime, changes: list[Change]
    ) -> None: ...

    async def rules(self) -> list[BranchRule]: ...

    async def get_rule(self, rule_id: UUID) -> BranchRule | None: ...

    async def save_rule(self, rule: BranchRule, change: Change, create: bool = False) -> BranchRule:
        """Insert or update a rule and record the change in one transaction.

        Raises:
            ConflictError: Another rule already has the supplier's NIT.
        """
        ...

    async def delete_rule(self, rule_id: UUID, change: Change) -> None: ...

    async def changes(self, limit: int = 50) -> list[Change]: ...


class ManagerPolicy(Protocol):
    async def is_manager(self, principal: Principal) -> bool: ...

    async def can_read(self, principal: Principal) -> bool: ...
