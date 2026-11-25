"""Ajuste de costos: the parameters, the supplier branch rules, their history and access."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.cost_settings.domain.entities import (
    FACTOR_GENERAL,
    FACTOR_WHOLESALE,
    THRESHOLD,
)
from tests.modules.cost_settings.conftest import COST_MANAGER, COST_READER
from tests.modules.supplier_supports.conftest import person

TEAM = "860000006"


async def test_the_defaults_are_the_quoters_until_bi_sets_one(settings) -> None:
    overview = await settings.overview(person(COST_READER))

    values = {item.spec.key: item for item in overview.parameters}
    assert values[FACTOR_GENERAL].value == Decimal("1.004") and values[FACTOR_GENERAL].is_default
    assert values[FACTOR_WHOLESALE].value == Decimal(1)
    assert values[THRESHOLD].value == Decimal("0.01")
    assert overview.can_manage is False
    threshold, factors = await settings.threshold_and_factors()
    assert threshold == Decimal("0.01") and factors["generales"] == Decimal("1.004")
    assert factors["mayoristas"] == Decimal(1)


async def test_a_parameter_changes_within_its_range_and_leaves_history(
    settings, cost_repository
) -> None:
    manager = person(COST_MANAGER)

    changed = await settings.set_parameters(
        manager, {THRESHOLD: "0,015", FACTOR_GENERAL: Decimal("1.004")}, "Ajuste de BI"
    )

    by_key = {item.spec.key: item for item in changed}
    assert by_key[THRESHOLD].value == Decimal("0.015") and not by_key[THRESHOLD].is_default
    # The factor sent with its own value is not a change.
    assert by_key[FACTOR_GENERAL].is_default
    (entry,) = cost_repository.history
    assert (entry.ref, entry.before["value"], entry.after["value"]) == (
        THRESHOLD,
        "0.01",
        "0.015000",
    )
    assert entry.actor == COST_MANAGER and entry.note == "Ajuste de BI"
    assert (await settings.threshold_and_factors())[0] == Decimal("0.015")

    with pytest.raises(InvalidInputError, match="entre 1 y 1.1"):
        await settings.set_parameters(manager, {FACTOR_GENERAL: "0.99"})
    with pytest.raises(InvalidInputError, match="desconocido"):
        await settings.set_parameters(manager, {"otro": "1"})
    with pytest.raises(InvalidInputError, match="número"):
        await settings.set_parameters(manager, {THRESHOLD: "uno"})
    with pytest.raises(InvalidInputError, match="no hay nada que cambiar"):
        await settings.set_parameters(manager, {THRESHOLD: "0.015"})


async def test_only_managers_change_and_readers_only_read(settings) -> None:
    reader = person(COST_READER)
    with pytest.raises(ForbiddenError, match="Solo quien gestiona"):
        await settings.set_parameters(reader, {THRESHOLD: "0.02"})
    with pytest.raises(ForbiddenError, match="Solo quien gestiona"):
        await settings.create_rule(reader, supplier_nit=TEAM, center_branches={"001": "002"})
    with pytest.raises(ForbiddenError, match="No tienes acceso"):
        await settings.overview(person("otro@jdh.com.co"))
    assert (await settings.overview(person("ti@jdh.com.co", admin=True))).can_manage


async def test_a_branch_rule_picks_the_branch_of_each_center(settings, cost_repository) -> None:
    manager = person(COST_MANAGER)

    rule = await settings.create_rule(
        manager,
        supplier_nit="860.000.006",
        supplier_name="  Team Foods ",
        center_branches={" 001 ": "002"},
        note="Lista de Antioquia",
    )

    assert (rule.supplier_nit, rule.general_branch, rule.center_branches) == (
        TEAM,
        "001",
        {"001": "002"},
    )
    assert rule.supplier_name == "Team Foods"
    antioquia = await settings.quote_rules("001")
    assert [(item.supplier_nit, item.branch, item.general_branch) for item in antioquia] == [
        (TEAM, "002", "001")
    ]
    # Another center takes the general branch; Nacional (no center) no rule at all.
    assert (await settings.quote_rules("003"))[0].branch is None
    assert await settings.quote_rules(None) == ()
    with pytest.raises(ConflictError, match="Ya hay una regla"):
        await settings.create_rule(manager, supplier_nit=TEAM, center_branches={"003": "002"})

    # Turned off, the supplier goes back to its highest quotation.
    off = await settings.update_rule(manager, rule.id, is_active=False, note="Compras revisa")
    assert not off.is_active and await settings.quote_rules("001") == ()
    assert cost_repository.history[-1].before["is_active"] is True
    with pytest.raises(InvalidInputError, match="queda igual"):
        await settings.update_rule(manager, rule.id, is_active=False, note="Compras revisa")
    moved = await settings.update_rule(manager, rule.id, center_branches={"001": "004"})
    assert moved.center_branches == {"001": "004"}

    await settings.delete_rule(manager, rule.id)
    assert await settings.quote_rules("001") == ()
    assert cost_repository.history[-1].after is None
    with pytest.raises(NotFoundError):
        await settings.delete_rule(manager, rule.id)


async def test_a_rule_is_checked_before_it_is_saved(settings) -> None:
    manager = person(COST_MANAGER)
    with pytest.raises(InvalidInputError, match="NIT del proveedor"):
        await settings.create_rule(manager, supplier_nit="abc", center_branches={"001": "002"})
    with pytest.raises(InvalidInputError, match="al menos un C.O."):
        await settings.create_rule(manager, supplier_nit=TEAM, center_branches={})
    with pytest.raises(InvalidInputError, match="ya toma la sucursal general"):
        await settings.create_rule(manager, supplier_nit=TEAM, center_branches={"001": "001"})
    with pytest.raises(InvalidInputError, match="Sucursal del C.O. 001"):
        await settings.create_rule(manager, supplier_nit=TEAM, center_branches={"001": "0 2"})


async def test_the_engine_reads_the_rules_once_in_a_while(settings, cost_repository) -> None:
    await settings.current()
    await settings.current()
    assert cost_repository.reads == 1
    # A change clears it: the next quotation takes it.
    await settings.set_parameters(person(COST_MANAGER), {THRESHOLD: "0.02"})
    assert (await settings.current()).value(THRESHOLD) == Decimal("0.02")
