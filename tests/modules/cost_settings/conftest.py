"""Fakes of the cost settings: a repository in memory and the managers."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.core.errors import ConflictError
from app.modules.cost_settings.application.service import CostSettingsService, clear_cache
from tests.modules.supplier_supports.conftest import FakeManagers

COST_MANAGER = "analista.bi@jdh.com.co"
COST_READER = "lector.bi@jdh.com.co"


class FakeCostSettingsRepository:
    def __init__(self) -> None:
        self.values: dict = {}
        self.stored: dict = {}
        self.history: list = []
        self.reads = 0

    async def parameters(self):
        self.reads += 1
        return dict(self.values)

    async def set_parameters(self, values, actor, at, changes):
        for key, value in values.items():
            self.values[key] = (value, actor, at)
        self.history.extend(changes)

    async def rules(self):
        return sorted(self.stored.values(), key=lambda rule: rule.supplier_nit)

    async def get_rule(self, rule_id):
        return self.stored.get(rule_id)

    async def save_rule(self, rule, change, create=False):
        if create and any(item.supplier_nit == rule.supplier_nit for item in self.stored.values()):
            raise ConflictError("Ya hay una regla para ese proveedor: edítala")
        self.stored[rule.id] = replace(rule)
        self.history.append(change)
        return rule

    async def delete_rule(self, rule_id, change):
        self.stored.pop(rule_id, None)
        self.history.append(change)

    async def changes(self, limit=50):
        return list(reversed(self.history))[:limit]


class ReadersAndManagers(FakeManagers):
    """``manage`` for the managers; ``read`` also for the readers."""

    def __init__(self, managers, readers) -> None:
        super().__init__(managers)
        self.readers = set(readers)

    async def can_read(self, principal):
        return principal.email in self.readers or await self.is_manager(principal)


@pytest.fixture(autouse=True)
def _no_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def cost_repository() -> FakeCostSettingsRepository:
    return FakeCostSettingsRepository()


@pytest.fixture
def settings(cost_repository) -> CostSettingsService:
    return CostSettingsService(
        cost_repository,
        managers=ReadersAndManagers([COST_MANAGER], [COST_READER]),
        threshold_default=Decimal("0.01"),
    )
