"""Opt-in: Nivel de servicio on PostgreSQL, migrated to head (0095 seeds the reasons).

Runs with ``COSTS_TEST_DATABASE_URL`` (never the shared database). The lines are dated in 2001
and tagged, so the test only ever sees its own rows.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import count
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.infrastructure.database import Database
from app.modules.identity.domain.principal import Principal
from app.modules.service_level.application.access import ServiceLevelAccess
from app.modules.service_level.application.service import ServiceLevelService
from app.modules.service_level.domain.entities import (
    DIMENSIONS,
    GROUP_SORTS,
    LINE_SORTS,
    Bands,
    Cuts,
    LineFilters,
)
from app.modules.service_level.infrastructure.models import (
    ServiceLevelClassModel,
    ServiceLevelClassRunModel,
    ServiceLevelLineModel,
    ServiceLevelReasonModel,
)
from app.modules.service_level.infrastructure.repository import SqlAlchemyServiceLevelRepository
from tests.modules.service_level.fakes import FakePolicy, FakeSource, line

pytestmark = pytest.mark.skipif(
    not os.getenv("COSTS_TEST_DATABASE_URL"),
    reason="Dedicated PostgreSQL integration URL not set",
)

PERSON = Principal(uuid4(), "abastecimiento@jdh.com.co", "Abastecimiento", False)
TODAY = date(2001, 5, 10)
MAY = LineFilters(since=date(2001, 5, 1), until=TODAY)


@pytest.fixture
async def world():
    database = Database(os.environ["COSTS_TEST_DATABASE_URL"])
    tag = uuid4().hex[:8]
    source = FakeSource(
        # The closed months the classes read: March and April.
        line(f"{tag}-m1", day=date(2001, 3, 15), sku=f"{tag}1", value="800", nit=f"{tag}a"),
        line(f"{tag}-a1", day=date(2001, 4, 10), sku=f"{tag}2", value="200", nit=f"{tag}b"),
        # May: one order with a short line and a full one, another order in Antioquia.
        line(
            f"{tag}-x1",
            day=date(2001, 5, 5),
            order=f"{tag}-o1",
            sku=f"{tag}1",
            ordered="10",
            dispatched="4",
            value="1000",
            nit=f"{tag}b",
        ),
        line(f"{tag}-x2", day=date(2001, 5, 5), order=f"{tag}-o1", sku=f"{tag}2", value="500"),
        line(
            f"{tag}-y1",
            day=date(2001, 5, 6),
            order=f"{tag}-o2",
            co="001",
            sku=f"{tag}1",
            value="300",
            supplier=("50", "ASCENDER S.A."),
        ),
    )
    repository = SqlAlchemyServiceLevelRepository(database)
    ticks = count()
    service = ServiceLevelService(
        repository,
        source,
        ServiceLevelAccess(FakePolicy(True), FakePolicy(read=True)),
        cuts=Cuts(),
        bands=Bands(),
        class_months=2,
        sync_days=100,
        start_date=date(2001, 3, 1),
        max_range_days=370,
        today=lambda: TODAY,
        clock=lambda: datetime(2026, 11, 26, tzinfo=UTC) + timedelta(seconds=next(ticks)),
    )
    created: list = []
    try:
        yield service, repository, source, tag, created
    finally:
        async with database.session_factory() as session, session.begin():
            await session.execute(
                delete(ServiceLevelLineModel).where(ServiceLevelLineModel.line_key.startswith(tag))
            )
            await session.execute(
                delete(ServiceLevelClassModel).where(ServiceLevelClassModel.key.startswith(tag))
            )
            await session.execute(
                delete(ServiceLevelClassRunModel).where(
                    ServiceLevelClassRunModel.period_from < date(2002, 1, 1)
                )
            )
            if created:
                await session.execute(
                    delete(ServiceLevelReasonModel).where(ServiceLevelReasonModel.id.in_(created))
                )
        await database.dispose()


async def test_the_figures_are_summed_in_sql(world) -> None:
    service, repository, source, tag, created = world
    report = await service.sync()
    reasons = {reason.name: reason for reason in await service.reasons()}
    agotado = reasons["AGOTADO PROVEEDOR"]
    await service.set_reason(PERSON, [f"{tag}-x1"], agotado.id, "llega el lunes")

    page = await service.lines(MAY, "value_pending", True, 1, 50)
    pending = await service.lines(
        LineFilters(since=MAY.since, until=MAY.until, pending=True, reason_id=agotado.id),
        "date",
        False,
        1,
        50,
    )
    board = await service.dashboard(MAY)

    assert report["read"] == 5 and report["classes"]["producto"]["A"] >= 1
    assert len(await service.reasons()) >= 46
    totals = page.kpis.totals
    assert (totals.orders, totals.orders_pending, totals.lines, totals.lines_pending) == (
        2,
        1,
        3,
        1,
    )
    assert totals.value == Decimal(1800) and totals.value_pending == Decimal(600)
    assert page.kpis.ns_orders == Decimal("0.5")
    first = page.items[0]
    assert first.line.line_key == f"{tag}-x1" and first.reason.responsible == "PROVEEDOR"
    assert (first.product_class, first.customer_class, first.note) == ("A", "D", "llega el lunes")
    assert pending.total == 1
    assert [(item.group.key, item.kpis.totals.lines) for item in board.by_co] == [
        ("001", 1),
        ("003", 2),
    ]
    assert [day for day, _ in board.daily] == [date(2001, 5, 5), date(2001, 5, 6)]
    assert [(g.name, g.totals.value_pending) for g in board.by_responsible] == [
        ("PROVEEDOR", Decimal(600))
    ]


async def test_every_grouping_and_sort_runs(world) -> None:
    service, _, _, tag, _ = world
    await service.sync()

    for dimension in DIMENSIONS:
        for sort in GROUP_SORTS:
            page = await service.breakdown(MAY, dimension, sort, True, 1, 10)
            assert page.total >= 0, (dimension, sort)
    for sort in LINE_SORTS:
        await service.lines(MAY, sort, False, 1, 10)
    suppliers = await service.breakdown(MAY, "supplier", "value", True, 1, 10)
    choices = await service.choices(MAY.since, MAY.until)
    curve = await service.pareto("producto", "003")
    searched = await service.lines(
        LineFilters(since=MAY.since, until=MAY.until, q=f"PV-{tag}-o2"), "date", False, 1, 10
    )

    assert [(g.key, g.totals.value) for g in suppliers.items] == [
        ("8", Decimal(1500)),
        ("50", Decimal(300)),
    ]
    assert choices.cos == {"001": "ANTIOQUIA", "003": "CENTRO"}
    assert curve.items >= 2 and curve.points[-1].value == 1
    assert searched.total == 1


async def test_a_line_gone_goes_or_stays_voided_with_its_reason(world) -> None:
    service, repository, source, tag, _ = world
    await service.sync()
    agotado = (await service.reasons())[0]
    await service.set_reason(PERSON, [f"{tag}-x1"], agotado.id, None)
    source.rows = [row for row in source.rows if row.line_key not in {f"{tag}-x1", f"{tag}-y1"}]

    report = await service.sync()
    page = await service.lines(MAY, "date", False, 1, 50)

    assert report["removed"] >= 1 and report["voided"] >= 1
    assert [row.line.line_key for row in page.items] == [f"{tag}-x2"]
    assert await repository.lines_by_key([f"{tag}-x1"]) == {}


async def test_the_reasons_catalog_on_postgres(world) -> None:
    service, _, _, tag, created = world

    reason = await service.create_reason(PERSON, f"PRUEBA {tag}", "LOGISTICA")
    created.append(reason.id)
    changed = await service.update_reason(PERSON, reason.id, name=f"PRUEBA {tag} DOS")

    assert changed.code == f"prueba-{tag}-dos" and changed.sort_order > 460
    assert "LOGISTICA" in await service.responsibles()
