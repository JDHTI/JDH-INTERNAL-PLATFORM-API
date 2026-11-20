"""A list update's last sales, read again in the background when the warehouse was slow."""

from __future__ import annotations

import asyncio

import pytest

from app.modules.quotations.application import sales_backfill
from app.modules.quotations.application.sales_backfill import SalesBackfill, longest_seconds
from app.modules.quotations.domain import rules, vocabulary
from tests.modules.quotations.conftest import LIST_MANAGER
from tests.modules.supplier_supports.conftest import person

SALE_FIELDS = tuple(rules.list_sale_fields(None))


class RecordingBackfill:
    def __init__(self) -> None:
        self.started: list[tuple] = []

    def start(self, quotation_id, skus, sale_keys) -> bool:
        self.started.append((quotation_id, tuple(skus), sale_keys))
        return True

    def running(self, quotation_id) -> bool:
        return any(item[0] == quotation_id for item in self.started)


class FlakyWarehouse:
    """Times out ``failures`` times, then answers like the real one."""

    def __init__(self, warehouse, failures: int) -> None:
        self.warehouse = warehouse
        self.failures = failures
        self.timeouts: list[float | None] = []

    async def list_last_sales(self, skus, months, customer_keys, timeout=None):
        self.timeouts.append(timeout)
        if self.failures:
            self.failures -= 1
            self.warehouse.sales_down = True
        else:
            self.warehouse.sales_down = False
        return await self.warehouse.list_last_sales(skus, months, customer_keys, timeout)


@pytest.fixture
def lister():
    return person(LIST_MANAGER)


def _waits():
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    return waits, sleep


async def test_a_slow_read_starts_the_retry_and_a_good_one_does_not(
    service, lister, warehouse
) -> None:
    service.sales_backfill = RecordingBackfill()
    warehouse.sales_down = True

    slow = await service.create_list_update(lister, price_list_id="10U")

    quotation_id, skus, keys = service.sales_backfill.started[0]
    assert quotation_id == slow.quotation.id and skus == ("2537", "26278")
    assert keys == ("1-900315138-001", "1-800111222-001")
    assert service.retrying_sales(slow.quotation.id)
    # A refresh that is slow again starts it again (it is the same one if still running).
    await service.refresh(lister, slow.quotation.id, slow.quotation.version)
    assert len(service.sales_backfill.started) == 2

    warehouse.sales_down = False
    await service.create_list_update(lister, price_list_id="10U")
    assert len(service.sales_backfill.started) == 2


async def test_the_sales_come_in_without_a_new_version(
    service, lister, warehouse, repository
) -> None:
    good = await service.create_list_update(lister, price_list_id="10U")
    warehouse.sales_down = True
    slow = await service.create_list_update(lister, price_list_id="10U")
    waits, sleep = _waits()
    flaky = FlakyWarehouse(warehouse, failures=1)
    backfill = SalesBackfill(repository, flaky, sleep=sleep)

    assert await backfill.run(slow.quotation.id, ["2537", "26278"], None)

    stored = repository.items[slow.quotation.id]
    assert vocabulary.SALES_NOT_READ not in stored.quotation.warnings
    assert stored.quotation.version == slow.quotation.version
    # Each line has what it would have had if the first read had answered.
    for line, expected in zip(stored.lines, good.lines, strict=True):
        assert {name: getattr(line, name) for name in SALE_FIELDS} == {
            name: getattr(expected, name) for name in SALE_FIELDS
        }
    assert stored.lines[0].last_sale_price is not None
    # Second try after 15 + 30 s, each read with its longer limit, then the two rechecks.
    assert waits == [15.0, 30.0, 5.0, 30.0]
    assert flaky.timeouts == [sales_backfill.READ_TIMEOUT] * 2


async def test_it_gives_up_and_never_writes_over_a_refresh(
    service, lister, warehouse, repository
) -> None:
    warehouse.sales_down = True
    slow = await service.create_list_update(lister, price_list_id="10U")
    waits, sleep = _waits()
    always_down = FlakyWarehouse(warehouse, failures=99)

    assert not await SalesBackfill(repository, always_down, sleep=sleep).run(
        slow.quotation.id, ["2537"], None
    )
    assert waits == list(sales_backfill.DELAYS)
    assert vocabulary.SALES_NOT_READ in repository.items[slow.quotation.id].quotation.warnings

    # A refresh read them meanwhile: the retry stops without reading.
    warehouse.sales_down = False
    await service.refresh(lister, slow.quotation.id, slow.quotation.version)
    late = FlakyWarehouse(warehouse, failures=0)
    assert not await SalesBackfill(repository, late, sleep=sleep).run(
        slow.quotation.id, ["2537"], None
    )
    assert late.timeouts == []


async def test_one_retry_per_list_update_in_the_background(
    service, lister, warehouse, repository
) -> None:
    warehouse.sales_down = True
    slow = await service.create_list_update(lister, price_list_id="10U")
    warehouse.sales_down = False
    _, sleep = _waits()
    backfill = SalesBackfill(repository, warehouse, sleep=sleep)

    assert backfill.start(slow.quotation.id, ["2537"], None)
    assert not backfill.start(slow.quotation.id, ["2537"], None)
    assert backfill.running(slow.quotation.id)
    await asyncio.gather(*sales_backfill._RUNNING.values())
    await asyncio.sleep(0)

    assert not backfill.running(slow.quotation.id)
    assert vocabulary.SALES_NOT_READ not in repository.items[slow.quotation.id].quotation.warnings
    # About 8 minutes of waits; with every read at its limit, about 21 minutes.
    assert longest_seconds() == 1250.0
