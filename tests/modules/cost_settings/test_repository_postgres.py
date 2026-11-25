"""Opt-in: the cost settings on PostgreSQL, with the two rules of the migration.

Runs with ``SUPPORT_REQUEST_TEST_DATABASE_URL`` (a scratch database at the head revision).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.core.errors import ConflictError
from app.infrastructure.database import Database
from app.modules.cost_settings.application.service import CostSettingsService
from app.modules.cost_settings.infrastructure.models import CostParameterModel
from app.modules.cost_settings.infrastructure.repository import SqlAlchemyCostSettingsRepository
from tests.modules.cost_settings.conftest import COST_MANAGER, ReadersAndManagers
from tests.modules.supplier_supports.conftest import person

pytestmark = pytest.mark.skipif(
    not os.getenv("SUPPORT_REQUEST_TEST_DATABASE_URL"),
    reason="Dedicated PostgreSQL integration URL not set",
)


@pytest.fixture
async def repository():
    database = Database(os.environ["SUPPORT_REQUEST_TEST_DATABASE_URL"])
    try:
        yield SqlAlchemyCostSettingsRepository(database)
    finally:
        await database.dispose()


async def test_the_migration_brings_team_foods_and_lactalis(repository) -> None:
    rules = {rule.supplier_nit: rule for rule in await repository.rules()}

    assert rules["860000006"].center_branches == {"001": "002"}
    assert rules["800245795"].center_branches == {"003": "003"}
    assert all(rules[nit].is_active and rules[nit].general_branch == "001" for nit in rules)
    seeded = [item for item in await repository.changes(200) if item.actor == "migracion-0092"]
    assert {item.ref for item in seeded} >= {"860000006", "800245795"}


async def test_rules_and_parameters_round_trip(repository) -> None:
    service = CostSettingsService(repository, ReadersAndManagers([COST_MANAGER], []))
    manager = person(COST_MANAGER)
    nit = f"9{uuid4().int % 10**9:09d}"

    rule = await service.create_rule(
        manager, supplier_nit=nit, supplier_name="Prueba", center_branches={"005": "002"}
    )
    try:
        assert (await repository.get_rule(rule.id)).center_branches == {"005": "002"}
        with pytest.raises(ConflictError):
            await service.create_rule(manager, supplier_nit=nit, center_branches={"001": "003"})
        off = await service.update_rule(manager, rule.id, is_active=False)
        assert off.is_active is False and off.updated_by == COST_MANAGER
        history = [item for item in await repository.changes(200) if item.ref == nit]
        assert [item.after["is_active"] for item in history[:2]] == [False, True]
    finally:
        await service.delete_rule(manager, rule.id)
    assert await repository.get_rule(rule.id) is None

    # A parameter is upserted with its change, then the next value replaces it.
    key = "umbral_negociacion"
    before = await repository.parameters()
    now = datetime.now(UTC)
    await repository.set_parameters({key: Decimal("0.0125")}, COST_MANAGER, now, [])
    await repository.set_parameters({key: Decimal("0.0130")}, COST_MANAGER, now, [])
    assert (await repository.parameters())[key][0] == Decimal("0.013")
    # Leave the scratch database as it was.
    if key in before:
        await repository.set_parameters({key: before[key][0]}, before[key][1], now, [])
    else:
        async with repository.database.session_factory() as session, session.begin():
            await session.execute(delete(CostParameterModel).where(CostParameterModel.key == key))
