"""Opt-in: supplier increase requests and their approval into the history, on PostgreSQL
(migrated to head). Runs with ``COSTS_TEST_DATABASE_URL`` (never the shared database)."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.infrastructure.database import Database
from app.modules.commercial_masters.domain.price_increases import (
    PriceIncreaseFilters,
    PriceIncreaseValues,
    Scope,
)
from app.modules.commercial_masters.infrastructure.models import PriceIncreaseModel
from app.modules.commercial_masters.infrastructure.price_increases_repository import (
    SqlAlchemyPriceIncreaseRepository,
)
from app.modules.costs.application.access import CostAccess
from app.modules.costs.application.increases import IncreaseService, LineChanges
from app.modules.costs.application.novelties import UploadedFile
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
from tests.modules.costs.fakes import NOW, TODAY, FakeGuard, FakePolicy, FakeStorage
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

    # The history of Cartagena's center (004): its own row and the national ones.
    history = SqlAlchemyPriceIncreaseRepository(database)
    scope = Scope(frozenset(skus), frozenset({"004"}))
    page = await history.page(PriceIncreaseFilters(scope=scope), 1, 50)
    assert {(item.values.sku, item.values.regional) for item in page.items} == {
        (skus[0], "CARTAGENA"),
        (skus[1], "NACIONAL"),
    }
    assert page.counts["bajo"] == 1
    nothing = await history.page(PriceIncreaseFilters(scope=Scope(frozenset(), frozenset())), 1, 5)
    assert nothing.total == 0
    rows = [row for row in await history.analysis_rows() if row.sku in skus]
    assert {(row.regional, row.occurrence) for row in rows if row.sku == skus[1]} == {
        ("NACIONAL", 1)
    }


async def test_increases_chained_on_the_same_day_are_kept_apart(world) -> None:
    _, database, _, skus = world
    history = SqlAlchemyPriceIncreaseRepository(database)
    first = PriceIncreaseValues(
        skus[0], increased_on=date(2021, 9, 24), old_cost=Decimal(15519), new_cost=Decimal(15984)
    )
    second = replace(first, old_cost=Decimal(15984), new_cost=Decimal(16463), occurrence=2)

    await history.upsert([first, second], "bi@jdh.com.co", NOW)

    stored = await history.existing([first.key, second.key])
    assert stored[second.key].occurrence == 2 and second.key.endswith("#2")
    page = await history.page(PriceIncreaseFilters(sku=skus[0]), 1, 10)
    # The later one of the day first.
    assert [item.values.new_cost for item in page.items] == [Decimal(16463), Decimal(15984)]


async def test_the_support_is_stored_with_the_request(world) -> None:
    increases, _, code, skus = world
    increases.support_guard = FakeGuard()
    request = await increases.create(
        PERSON, code, [LineInput(skus[0], Decimal("1100"), increased_on=TODAY)]
    )

    attached = await increases.attach_support(
        PERSON, request.id, UploadedFile("carta.pdf", b"%PDF-1.7 carta")
    )
    listed = await increases.page(None, code, 1, 10)

    assert attached.support.file_name == "carta.pdf" and attached.support.size_bytes == 14
    assert attached.support.uploaded_by == PERSON.email
    assert listed.items[0].support.storage_key == attached.support.storage_key
    removed = await increases.remove_support(PERSON, request.id)
    assert removed.support is None
