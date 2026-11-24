"""Calculating, closing and reopening a month."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.support_settlements.application.service import SettlementService
from app.modules.support_settlements.domain.entities import CLOSED, DRAFT
from app.modules.support_settlements.infrastructure.export_file import settlement_workbook
from tests.modules.fakes import principal
from tests.modules.support_settlements.fakes import (
    FakeMasters,
    FakeRepository,
    FakeSales,
    Policy,
    customer,
    product,
    quote,
    sale,
    support,
)

TODAY = date(2026, 11, 24)
ANALYST = principal("analista.bi@jdh.com.co")
READER = principal("lector@jdh.com.co")
LEADER = principal("lider.bi@jdh.com.co")
OUTSIDER = principal("otro@jdh.com.co")


def october(**values):
    return sale(sale_date=date(2026, 10, 15), **values)


@pytest.fixture
def repository() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def sales() -> FakeSales:
    return FakeSales([october(), october(document="FEV-2")], {"18369": [quote("9142.67")]})


@pytest.fixture
def service(repository, sales) -> SettlementService:
    return SettlementService(
        repository,
        FakeMasters([support()], [product()], [customer()]),
        sales,
        Policy(managers={ANALYST.email}, readers={READER.email}),
        Policy(managers={LEADER.email}),
        start_period="2026-10",
        writer=settlement_workbook,
        today=lambda: TODAY,
    )


async def test_permissions(service) -> None:
    analyst = await service.permissions(ANALYST)
    reader = await service.permissions(READER)
    outsider = await service.permissions(OUTSIDER)

    assert (analyst.can_read, analyst.can_manage, analyst.can_reopen) == (True, True, False)
    assert (reader.can_read, reader.can_manage) == (True, False)
    assert not outsider.can_read
    assert (await service.permissions(LEADER)).can_reopen


async def test_recalculate_stores_a_draft_and_close_freezes_it(service, repository) -> None:
    draft = await service.recalculate(ANALYST, "2026-10")

    assert draft.status == DRAFT and draft.supported_lines == 2 and draft.amount > 0

    closed = await service.close(ANALYST, "2026-10")
    assert closed.status == CLOSED and closed.closed_by == ANALYST.email
    with pytest.raises(ConflictError, match="cerrada"):
        await service.recalculate(ANALYST, "2026-10")
    with pytest.raises(ConflictError):
        await service.close(ANALYST, "2026-10")


async def test_only_a_finished_month_closes(service) -> None:
    await service.recalculate(ANALYST, "2026-11")

    with pytest.raises(InvalidInputError, match="terminado"):
        await service.close(ANALYST, "2026-11")


async def test_months_before_the_start_or_in_the_future_are_refused(service) -> None:
    with pytest.raises(InvalidInputError, match="empieza"):
        await service.recalculate(ANALYST, "2026-09")
    with pytest.raises(InvalidInputError, match="no ha empezado"):
        await service.recalculate(ANALYST, "2026-12")
    with pytest.raises(InvalidInputError):
        await service.recalculate(ANALYST, "2026-13")


async def test_reading_calculating_and_reopening_need_their_permission(service) -> None:
    with pytest.raises(ForbiddenError):
        await service.recalculate(READER, "2026-10")
    with pytest.raises(ForbiddenError):
        await service.periods(OUTSIDER)
    await service.close(ANALYST, "2026-10")
    with pytest.raises(ForbiddenError, match="líder"):
        await service.reopen(ANALYST, "2026-10")

    reopened = await service.reopen(LEADER, "2026-10")

    assert reopened.status == DRAFT and reopened.reopened_by == LEADER.email
    assert (await service.recalculate(ANALYST, "2026-10")).status == DRAFT


async def test_periods_offer_the_current_and_previous_month(service) -> None:
    await service.recalculate(ANALYST, "2026-10")

    found = await service.periods(READER)

    assert [item.period for item in found] == ["2026-11", "2026-10"]
    assert found[0].is_current and not found[0].is_finished and found[0].settlement is None
    assert found[1].is_finished and found[1].settlement is not None


async def test_reading_a_month_not_calculated_is_404(service) -> None:
    with pytest.raises(NotFoundError):
        await service.summary(READER, "2026-10")


async def test_summary_lines_and_workbook(service) -> None:
    await service.recalculate(ANALYST, "2026-10")

    settlement, suppliers = await service.summary(READER, "2026-10")
    lines, total = await service.lines(READER, "2026-10", page=1, size=10)
    workbook = await service.export(READER, "2026-10", "233")

    assert suppliers[0]["supplier_code"] == "233" and suppliers[0]["amount"] == settlement.amount
    assert total == 2 and lines[0]["items"][0]["status"] == "liquidado"
    assert workbook.filename == "liquidacion-apoyos-2026-10-proveedor-233.xlsx"
    assert workbook.content[:2] == b"PK"


class SlowSales(FakeSales):
    """A DWH read that waits until the test lets it go."""

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.release = asyncio.Event()
        self.started = asyncio.Event()

    async def sales(self, start, following, product_keys):
        self.started.set()
        await self.release.wait()
        return await super().sales(start, following, product_keys)


async def test_a_recalculation_finishes_when_the_request_is_cancelled(repository) -> None:
    slow = SlowSales([october()], {"18369": [quote("9142.67")]})
    service = SettlementService(
        repository,
        FakeMasters([support()], [product()], [customer()]),
        slow,
        Policy(managers={ANALYST.email}),
        Policy(),
        start_period="2026-10",
        today=lambda: TODAY,
    )
    request = asyncio.create_task(service.recalculate(ANALYST, "2026-10"))
    await slow.started.wait()

    request.cancel()  # the proxy hung up
    with pytest.raises(asyncio.CancelledError):
        await request
    slow.release.set()
    for _ in range(20):
        if "2026-10" in repository.items:
            break
        await asyncio.sleep(0)

    assert repository.items["2026-10"].supported_lines == 1


async def test_the_job_refreshes_open_months_and_leaves_closed_ones(service, repository) -> None:
    await service.close(ANALYST, "2026-10")

    done = await service.refresh_open()

    assert done["periodos"]["2026-10"] == "cerrado"
    assert repository.items["2026-11"].status == DRAFT
    assert repository.items["2026-10"].status == CLOSED
