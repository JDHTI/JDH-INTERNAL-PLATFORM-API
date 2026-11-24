"""Opt-in: the settlement repository on PostgreSQL, migrated to head (0085).

Runs with ``SUPPORT_SETTLEMENTS_TEST_DATABASE_URL`` (never the shared database).
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete

from app.core.errors import ConflictError
from app.infrastructure.database import Database
from app.modules.support_settlements.domain.rules import build_settlement
from app.modules.support_settlements.infrastructure.models import SupportSettlementModel
from app.modules.support_settlements.infrastructure.repository import (
    SqlAlchemySettlementRepository,
)
from tests.modules.support_settlements.fakes import customer, product, quote, sale, support

pytestmark = pytest.mark.skipif(
    not os.getenv("SUPPORT_SETTLEMENTS_TEST_DATABASE_URL"),
    reason="Dedicated PostgreSQL integration URL not set",
)

# A month no real settlement uses.
PERIOD = "2001-03"
NOW = datetime(2001, 4, 2, tzinfo=UTC)


@pytest.fixture
async def repository():
    database = Database(os.environ["SUPPORT_SETTLEMENTS_TEST_DATABASE_URL"])
    try:
        yield SqlAlchemySettlementRepository(database)
    finally:
        async with database.session_factory() as session, session.begin():
            await session.execute(
                delete(SupportSettlementModel).where(SupportSettlementModel.period == PERIOD)
            )
        await database.dispose()


def result(*lines):
    first = support(valid_from=date(2001, 1, 1), valid_to=None)
    second = support(
        valid_from=date(2001, 1, 1),
        valid_to=None,
        calc_method="valor_unidad",
        supplier_value=Decimal("100"),
        value_unit="unidad",
        status="pendiente_revision",
    )
    return build_settlement(
        sales=[*lines],
        supports=[first, second],
        products={"18369": product()},
        customers={"900973779-001": customer()},
        quotes={"18369": [quote("9142.67", since=date(2001, 1, 1))]},
    )


async def test_replace_close_reopen_and_aggregates(repository) -> None:
    day = date(2001, 3, 10)
    first = result(sale(sale_date=day), sale(sale_date=day, document="FEV-2"))
    stored = await repository.replace(PERIOD, first, "bi", NOW)

    expected = sum(line.amount for line in first.sales)
    assert stored.status == "borrador" and stored.supported_lines == 2
    assert stored.amount == expected
    assert stored.flagged_amount == Decimal("9000.0000")

    # A recalculation replaces the lines.
    again = await repository.replace(PERIOD, result(sale(sale_date=day)), "bi", NOW)
    assert again.supported_lines == 1 and again.version > stored.version
    lines, total = await repository.lines(
        again.id, supplier_code="233", sku=None, nit=None, status=None, page=1, size=10
    )
    assert total == 1 and len(lines[0]["items"]) == 2
    flagged, flagged_total = await repository.lines(
        again.id, supplier_code=None, sku=None, nit=None, status="en_revision", page=1, size=10
    )
    assert flagged_total == 1

    suppliers = await repository.suppliers(again.id)
    references = await repository.references(again.id, "233")
    by_regional = await repository.breakdown(again.id, "regional", None)
    assert suppliers[0]["supplier_code"] == "233" and suppliers[0]["references"] == 1
    assert references[0]["sku"] == "18369" and references[0]["supported_lines"] == 1
    assert by_regional[0]["key"] == "ANTIOQUIA" and by_regional[0]["lines"] == 1
    sales, refs = await repository.export_rows(again.id, None)
    assert len(sales) == 1 and len(refs) == 1

    closed = await repository.replace(PERIOD, result(sale(sale_date=day)), "bi", NOW, close=True)
    assert closed.status == "cerrado" and closed.closed_by == "bi"
    with pytest.raises(ConflictError):
        await repository.replace(PERIOD, first, "bi", NOW)

    reopened = await repository.reopen(PERIOD, "lider", NOW)
    assert reopened.status == "borrador" and reopened.reopened_by == "lider"
    assert [item.period for item in await repository.list() if item.period == PERIOD] == [PERIOD]
