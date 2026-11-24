"""Opt-in: supplier increase requests and their approval into the history, on PostgreSQL
(migrated to head). Runs with ``COSTS_TEST_DATABASE_URL`` (never the shared database)."""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.infrastructure.database import Database
from app.modules.commercial_masters.infrastructure.models import PriceIncreaseModel
from app.modules.commercial_masters.infrastructure.price_increases_repository import (
    SqlAlchemyPriceIncreaseRepository,
)
from app.modules.costs.application.access import CostAccess
from app.modules.costs.application.increases import IncreaseService, LineChanges
from app.modules.costs.domain.increases import REST, LineInput
from app.modules.costs.infrastructure.increase_file import (
    IncreaseWorkbookReader,
    template_workbook,
    workbook_name,
)
from app.modules.costs.infrastructure.increase_repository import SqlAlchemyIncreaseRepository
from app.modules.costs.infrastructure.ledger import CommercialIncreaseLedger
from app.modules.costs.infrastructure.models import CostIncreaseRequestModel
from app.modules.identity.domain.principal import Principal
from tests.modules.costs.fakes import NOW, TODAY, FakePolicy, FakeStorage
from tests.modules.costs.increase_fakes import FakeItems, item

pytestmark = pytest.mark.skipif(
    not os.getenv("COSTS_TEST_DATABASE_URL"),
    reason="Dedicated PostgreSQL integration URL not set",
)

PERSON = Principal(uuid4(), "compras@jdh.com.co", "Compras", False)


class Reasons:
    async def __call__(self):
        return []


@pytest.fixture
async def world():
    database = Database(os.environ["COSTS_TEST_DATABASE_URL"])
    code = f"T{uuid4().hex[:6]}"
    skus = [f"ZZ{uuid4().hex[:8]}", f"ZZ{uuid4().hex[:8]}"]
    increases = IncreaseService(
        SqlAlchemyIncreaseRepository(database),
        FakeItems(item(skus[0], "1000", icui="0.2"), item(skus[1], "500"), code=code),
        CommercialIncreaseLedger(SqlAlchemyPriceIncreaseRepository(database)),
        CostAccess(FakePolicy(), FakePolicy(), FakePolicy(manage=True), FakePolicy()),
        FakeStorage(),
        IncreaseWorkbookReader(),
        template_workbook,
        workbook_name,
        Reasons(),
        max_upload_bytes=1024 * 1024,
        today=lambda: TODAY,
        clock=lambda: NOW,
    )
    try:
        yield increases, database, code, skus
    finally:
        async with database.session_factory() as session, session.begin():
            await session.execute(
                delete(PriceIncreaseModel).where(PriceIncreaseModel.sku.in_(skus))
            )
            await session.execute(
                delete(CostIncreaseRequestModel).where(
                    CostIncreaseRequestModel.supplier_code == code
                )
            )
        await database.dispose()


async def test_an_approved_request_lands_in_the_history_as_pending(world) -> None:
    increases, database, code, skus = world
    request = await increases.create(
        PERSON,
        code,
        [
            LineInput(
                skus[0],
                Decimal("1100"),
                increased_on=date(2026, 12, 1),
                regular_discount_pct=Decimal("0.03"),
                regional="CARTAGENA",
                public_price=Decimal("1990"),
            ),
            LineInput(skus[0], Decimal("1050"), increased_on=date(2026, 12, 1), regional=REST),
            LineInput(skus[1], Decimal("450"), increased_on=date(2026, 12, 1)),
        ],
    )
    first = next(line for line in request.lines if line.regional == "CARTAGENA")
    assert first.public_price == Decimal("1990.00") and first.regionals == ("CARTAGENA",)
    assert first.new_cost_with_tax == Decimal("1320.0000")
    rest = next(line for line in request.lines if line.regional == REST)
    assert len(rest.regionals) == 5 and "CARTAGENA" not in rest.regionals

    second = next(line for line in request.lines if line.sku == skus[1])
    request = await increases.update_line(
        PERSON, request.id, second.id, LineChanges(public_price=Decimal("700"))
    )
    second = next(line for line in request.lines if line.sku == skus[1])
    assert second.public_price == Decimal("700.00") and second.regional == "NACIONAL"

    page = await increases.page("en_revision", code, 1, 10)
    assert page.total == 1 and page.items[0].line_count == 3 and page.items[0].accepted_count == 3

    approved = await increases.approve(PERSON, request.id, "Aprobada con la carta")

    assert approved.status == "aprobada" and all(line.ledger_id for line in approved.lines)
    async with database.session_factory() as session:
        rows = list(
            await session.scalars(
                select(PriceIncreaseModel).where(PriceIncreaseModel.sku.in_(skus))
            )
        )
    by_regional = {(row.sku, row.regional): row for row in rows}
    # Cartagena, five rows for the rest and the national one of the second reference.
    assert len(rows) == 7 and (skus[1], "NACIONAL") in by_regional
    row = by_regional[(skus[0], "CARTAGENA")]
    assert row.source == "solicitud" and row.request_line_id == first.id
    assert row.row_key == f"{skus[0]}|2026-12-01|{NOW.date().isoformat()}|CARTAGENA"
    assert row.system_updated_on is None and row.notified and row.status == "subio"
    assert row.notified_on == NOW.date() and row.increased_on == date(2026, 12, 1)
    assert row.new_cost_with_tax == Decimal("1320.0000")
    assert "PVP sin IVA 1990.00" in row.observations
    assert by_regional[(skus[0], "BOGOTA")].new_cost == Decimal("1050")
    assert by_regional[(skus[0], "BOGOTA")].request_line_id == rest.id
    assert by_regional[(skus[1], "NACIONAL")].status == "bajo"
    assert by_regional[(skus[1], "NACIONAL")].row_key.count("|") == 2
