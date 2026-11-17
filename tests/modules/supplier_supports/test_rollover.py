"""Programmed supports replace the previous one: the daily job and the alerts (rules v2)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from app.modules.supplier_supports.application.rollover import SupportRolloverService
from app.modules.supplier_supports.domain.entities import SupportData, Trace
from tests.modules.supplier_supports.conftest import (
    FAMILIA_ID,
    FakeSupportRepository,
    FixedCalendar,
)

D = Decimal


def tactic(**values) -> SupportData:
    base = {
        "kind": "tactico_cliente",
        "calc_method": "pct_costo",
        "commercial_supplier_id": FAMILIA_ID,
        "supplier_value": D("0.05"),
        "customer_nit": "800116299",
        "product_sku": "1128",
        "valid_from": date(2026, 7, 1),
        "valid_to": None,
    }
    return SupportData(**{**base, **values})


async def store(repo, data):
    return await repo.create(uuid4(), data, "bi", datetime.now(UTC), Trace("importacion"))


async def test_a_crossing_from_before_the_rule_is_cut_the_day_before() -> None:
    repo = FakeSupportRepository()
    old = await store(repo, tactic())
    new = await store(repo, tactic(valid_from=date(2026, 10, 1), supplier_value=D("0.07")))
    other_client = await store(repo, tactic(customer_nit="900430148"))

    summary = await SupportRolloverService(repo, FixedCalendar()).run()

    assert summary["cut"] == 1
    assert repo.items[old.id].data.valid_to == date(2026, 9, 30)
    assert repo.items[new.id].version == 1 and repo.items[other_client.id].version == 1
    # Once chained nothing is left to do.
    assert (await SupportRolloverService(repo, FixedCalendar()).run())["cut"] == 0


async def test_the_alerts_list_what_a_programmed_support_replaces() -> None:
    repo = FakeSupportRepository()
    current = await store(repo, tactic())
    programmed = await store(repo, tactic(valid_from=date(2026, 12, 1)))
    await store(repo, tactic(product_sku="8749", valid_from=date(2026, 1, 1)))

    alerts = await SupportRolloverService(repo, FixedCalendar()).replacements()

    assert len(alerts) == 1
    alert = alerts[0]
    assert (alert.programmed.id, alert.previous.id) == (programmed.id, current.id)
    assert alert.previous_ends == date(2026, 11, 30) and alert.chained is False
