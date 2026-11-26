"""The service of Nivel de servicio with doubles: the job, the reasons and the figures."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.identity.domain.principal import Principal
from app.modules.service_level.domain.entities import LineFilters
from tests.modules.service_level.fakes import (
    DAY,
    FakeRepository,
    FakeSource,
    line,
    make_service,
)

PERSON = Principal(uuid4(), "abastecimiento@jdh.com.co", "Abastecimiento", False)
REASONS = (("AGOTADO PROVEEDOR", "PROVEEDOR"), ("ERROR DIGITACION", "COMERCIAL"))
NOVEMBER = LineFilters(since=date(2026, 11, 1), until=date(2026, 11, 26))


def world(*lines, **options):
    repository = FakeRepository(REASONS)
    source = FakeSource(*lines)
    return make_service(repository, source, **options), repository, source


async def test_the_first_run_copies_from_the_start_a_month_at_a_time() -> None:
    service, repository, source = world(
        line("sep", day=date(2026, 9, 10), value="500", sku="2"),
        line("oct", day=date(2026, 10, 5), value="1500"),
        line("nov", day=DAY, ordered="10", dispatched="6"),
    )

    report = await service.sync()

    assert source.reads[0] == (date(2026, 9, 1), date(2026, 9, 30))
    assert len(source.reads) == 3 and report["read"] == 3 and report["upserted"] == 3
    assert set(repository.stored) == {"sep", "oct", "nov"}
    # The closed months (September, October) are classified on the first run.
    assert report["classes"]["producto"]["A"] == 1
    assert repository.runs[0].period_from == date(2026, 9, 1)


async def test_later_runs_reread_45_days_and_classify_once_a_month() -> None:
    service, repository, source = world(line("nov", day=DAY))
    await service.sync()
    source.reads.clear()

    report = await service.sync()

    assert source.reads == [
        (date(2026, 10, 13), date(2026, 10, 31)),
        (date(2026, 11, 1), date(2026, 11, 26)),
    ]
    assert report["classes"] is None and len(repository.runs) == 1


async def test_a_line_gone_from_the_warehouse_goes_unless_it_has_a_reason() -> None:
    service, repository, source = world(
        line("kept", ordered="10", dispatched="4"), line("gone", ordered="5", dispatched="1")
    )
    await service.sync()
    await service.set_reason(PERSON, ["kept"], repository.reason("AGOTADO PROVEEDOR").id, None)
    source.rows = [line("other")]
    repository.synced = dict.fromkeys(repository.synced, repository.synced["kept"].replace(hour=1))

    report = await service.sync()

    assert report["removed"] == 1 and report["voided"] == 1
    assert "gone" not in repository.stored and "kept" in repository.voided
    totals = (await service.lines(NOVEMBER, "date", False, 1, 50)).kpis.totals
    assert totals.lines == 1


async def test_an_empty_read_keeps_the_copy() -> None:
    service, repository, source = world(line("a"))
    await service.sync()
    source.rows = []

    report = await service.sync()

    assert report["removed"] == 0 and "a" in repository.stored


async def test_the_job_waits_for_the_warehouse_view() -> None:
    service, repository, source = world(line("a"))
    source.is_available = False

    report = await service.sync()

    assert report["skipped"] and not repository.stored and not source.reads


async def test_only_pending_lines_take_a_reason_and_a_manager_gives_it() -> None:
    service, repository, _ = world(
        line("short", ordered="10", dispatched="7"), line("full", ordered="3", dispatched="3")
    )
    await service.sync()
    reason = repository.reason("AGOTADO PROVEEDOR")

    rows = await service.set_reason(PERSON, ["short"], reason.id, "  llega   el lunes ")

    assert rows[0].reason == reason and rows[0].note == "llega el lunes"
    with pytest.raises(InvalidInputError):
        await service.set_reason(PERSON, ["full"], reason.id, None)
    with pytest.raises(NotFoundError):
        await service.set_reason(PERSON, ["nope"], reason.id, None)
    cleared = await service.set_reason(PERSON, ["short"], None, None)
    assert cleared[0].reason is None
    reader = make_service(repository, manage=False)
    with pytest.raises(ForbiddenError):
        await reader.set_reason(PERSON, ["short"], reason.id, None)


async def test_an_inactive_reason_is_not_given() -> None:
    service, repository, _ = world(line("short", ordered="10", dispatched="7"))
    await service.sync()
    reason = repository.reason("ERROR DIGITACION")
    await service.update_reason(PERSON, reason.id, is_active=False)

    with pytest.raises(InvalidInputError):
        await service.set_reason(PERSON, ["short"], reason.id, None)


async def test_the_reasons_catalog_is_kept_by_the_managers() -> None:
    service, repository, _ = world()

    created = await service.create_reason(PERSON, " pedido   sin cupo ", "cartera")
    renamed = await service.update_reason(PERSON, created.id, responsible="tesoreria")

    assert (created.name, created.code, created.sort_order) == (
        "PEDIDO SIN CUPO",
        "pedido-sin-cupo",
        30,
    )
    assert renamed.responsible == "TESORERIA"
    assert await service.responsibles() == ["COMERCIAL", "PROVEEDOR", "TESORERIA"]
    with pytest.raises(ConflictError):
        await service.create_reason(PERSON, "Agotado proveedor", "PROVEEDOR")
    with pytest.raises(ConflictError):
        await service.update_reason(PERSON, created.id, name="error digitacion")
    with pytest.raises(InvalidInputError):
        await service.create_reason(PERSON, "   ", "PROVEEDOR")
    with pytest.raises(ForbiddenError):
        await make_service(repository, manage=False).create_reason(PERSON, "OTRO", "COMERCIAL")


async def test_the_dashboard_sums_by_co_day_and_responsible() -> None:
    service, repository, _ = world(
        line("a1", order="1", co="003", ordered="10", dispatched="5", value="1000"),
        line("a2", order="1", co="003", value="1000"),
        line("b1", order="2", co="001", day=date(2026, 11, 21), value="2000"),
    )
    await service.sync()
    await service.set_reason(PERSON, ["a1"], repository.reason("AGOTADO PROVEEDOR").id, None)

    board = await service.dashboard(NOVEMBER)

    assert board.kpis.totals.orders == 2 and board.kpis.totals.orders_pending == 1
    assert board.kpis.totals.value_pending == Decimal(500)
    assert board.kpis.ns_value == Decimal("0.875")
    assert [(item.group.key, item.kpis.ns_orders) for item in board.by_co] == [
        ("001", 1),
        ("003", 0),
    ]
    assert [day for day, _ in board.daily] == [DAY, date(2026, 11, 21)]
    assert [(g.name, g.totals.value_pending) for g in board.by_responsible] == [
        ("PROVEEDOR", Decimal(500))
    ]


async def test_a_breakdown_groups_and_checks_its_arguments() -> None:
    service, _, _ = world(
        line("a", supplier=("8", "TEAM FOODS"), value="300", ordered="3", dispatched="2"),
        line("b", supplier=("50", "ASCENDER S.A."), value="900"),
    )
    await service.sync()

    page = await service.breakdown(NOVEMBER, "supplier", "value", True, 1, 50)

    assert [(g.key, g.totals.value_served) for g in page.items] == [
        ("50", Decimal(900)),
        ("8", Decimal(200)),
    ]
    with pytest.raises(InvalidInputError):
        await service.breakdown(NOVEMBER, "planet", "value", True, 1, 50)
    with pytest.raises(InvalidInputError):
        await service.breakdown(NOVEMBER, "supplier", "rank", True, 1, 50)


async def test_the_period_is_the_current_month_by_default_and_has_a_limit() -> None:
    service, _, _ = world()

    assert service.period(None, None) == (date(2026, 11, 1), date(2026, 11, 26))
    assert service.period(date(2026, 10, 5), None) == (date(2026, 10, 5), date(2026, 11, 26))
    with pytest.raises(InvalidInputError):
        service.period(date(2026, 11, 20), date(2026, 11, 1))
    with pytest.raises(InvalidInputError):
        service.period(date(2024, 1, 1), date(2026, 11, 1))


async def test_the_classes_join_the_lines_and_feed_the_pareto() -> None:
    service, _, _ = world(
        line("big", day=date(2026, 10, 3), sku="1", value="800", nit="1"),
        line("small", day=date(2026, 10, 3), sku="2", value="200", nit="2"),
        line("now", sku="1", nit="2", ordered="2", dispatched="1"),
    )
    await service.sync()

    page = await service.lines(NOVEMBER, "date", False, 1, 50)
    curve = await service.pareto("producto", "003")
    by_class = LineFilters(since=NOVEMBER.since, until=NOVEMBER.until, customer_class="D")
    customers = await service.lines(by_class, "date", False, 1, 50)

    # 800 of 1000 is exactly 80 %: still A; the customer with the last 20 % is D.
    assert (page.items[0].product_class, page.items[0].customer_class) == ("A", "D")
    assert curve.items == 2 and curve.points[-1].value == 1
    assert customers.total == 1
    with pytest.raises(ForbiddenError):
        await make_service(manage=False).recompute_classes(PERSON)
