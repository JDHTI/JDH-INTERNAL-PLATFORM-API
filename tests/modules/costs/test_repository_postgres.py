"""Opt-in: the novelties repository on PostgreSQL, migrated to head (0080 seeds the reasons).

Runs with ``COSTS_TEST_DATABASE_URL`` (never the shared database).
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal
from itertools import count
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.infrastructure.database import Database
from app.modules.costs.application.access import CostAccess
from app.modules.costs.application.novelties import NoveltyService, UploadedFile
from app.modules.costs.domain.novelties import (
    AWAITING_CREDIT_NOTE,
    PENDING,
    RESOLVED,
    VOIDED,
    NoveltyFilters,
)
from app.modules.costs.infrastructure.models import (
    CostCreditNoteModel,
    CostNoveltyModel,
)
from app.modules.costs.infrastructure.repository import SqlAlchemyNoveltyRepository
from app.modules.identity.domain.principal import Principal
from tests.modules.costs.fakes import NOW, FakeGuard, FakePolicy, FakeSource, FakeStorage, line

pytestmark = pytest.mark.skipif(
    not os.getenv("COSTS_TEST_DATABASE_URL"),
    reason="Dedicated PostgreSQL integration URL not set",
)

PERSON = Principal(uuid4(), "compras@jdh.com.co", "Compras", False)
PDF = b"%PDF-1.7 nota"
# Far from real entries, so the test only ever sees its own rows.
DAY = date(2001, 3, 15)


@pytest.fixture
async def world():
    database = Database(os.environ["COSTS_TEST_DATABASE_URL"])
    tag = uuid4().hex[:8]
    source = FakeSource(
        line(f"{tag}-up", day=DAY, price="52500", quotation="50000", sku="2548", nit=f"9{tag}"),
        line(f"{tag}-up2", day=DAY, price="1030", quotation="1000", sku="7053", nit=f"9{tag}"),
        line(f"{tag}-none", day=DAY, price="1000", quotation=None, sku="10042"),
        line(f"{tag}-ok", day=DAY, price="1000", quotation="1000", sku="1001"),
    )
    repository = SqlAlchemyNoveltyRepository(database)
    storage = FakeStorage()
    ticks = count()
    service = NoveltyService(
        repository,
        source,
        CostAccess(FakePolicy(True), FakePolicy(True), FakePolicy(), FakePolicy()),
        storage,
        FakeGuard(),
        threshold=Decimal("0.001"),
        sync_days=10,
        start_date=DAY - timedelta(days=5),
        today=lambda: DAY + timedelta(days=1),
        clock=lambda: NOW + timedelta(seconds=next(ticks)),
    )
    try:
        yield service, repository, source, storage, tag
    finally:
        async with database.session_factory() as session, session.begin():
            await session.execute(
                delete(CostNoveltyModel).where(CostNoveltyModel.line_key.startswith(tag))
            )
            await session.execute(
                delete(CostCreditNoteModel).where(CostCreditNoteModel.supplier_nit == f"9{tag}")
            )
        await database.dispose()


async def test_the_whole_life_of_a_novelty(world) -> None:
    service, repository, source, storage, tag = world

    report = await service.sync()
    assert report["created"] == 3 and report["lines_read"] == 4

    page = await service.page(NoveltyFilters(since=DAY, until=DAY), "difference_pct", True, 1, 50)
    assert [item.line.line_key for item in page.items] == [f"{tag}-up", f"{tag}-up2", f"{tag}-none"]
    assert page.counts["pendiente"] == 3 and page.counts["total"] == 3
    assert page.items[0].line.supplier_code == "399"

    reasons = {reason.name: reason for reason in await service.reasons()}
    assert len(reasons) == 26 and reasons["SOBRECOSTO"].requires_credit_note
    increase = reasons["INCREMENTO NO ANUNCIADO"]
    up = await service.set_reason_for_line(PERSON, f"{tag}-up", increase.id)
    up2 = await service.set_reason_for_line(PERSON, f"{tag}-up2", increase.id)
    assert up.status == AWAITING_CREDIT_NOTE and up.reason.code == "incremento-no-anunciado"

    briefs = await repository.briefs_between(DAY, DAY)
    assert briefs[f"{tag}-up"].reason_name == "INCREMENTO NO ANUNCIADO"
    assert f"{tag}-ok" not in briefs

    noted = await service.attach_credit_note(
        PERSON, up.id, number="NC-1", amount="2500", upload=UploadedFile("nc.pdf", PDF)
    )
    shared = await service.attach_credit_note(
        PERSON, up2.id, number="nc-1", amount="50", upload=None
    )
    assert noted.status == RESOLVED and shared.credit_note.id == noted.credit_note.id
    found = await service.page(NoveltyFilters(q="NC-1", since=DAY, until=DAY), "date", True, 1, 50)
    assert {item.line.line_key for item in found.items} == {f"{tag}-up", f"{tag}-up2"}

    await service.detach_credit_note(PERSON, up.id)
    assert len(storage.objects) == 1
    back = await service.detach_credit_note(PERSON, up2.id)
    assert storage.objects == {} and back.status == AWAITING_CREDIT_NOTE

    # The reason stops needing a credit note: its novelties resolve.
    await service.update_reason(PERSON, increase.id, requires_credit_note=False)
    try:
        assert (await service.get(up.id)).status == RESOLVED
    finally:
        await service.update_reason(PERSON, increase.id, requires_credit_note=True)
    assert (await service.get(up.id)).status == AWAITING_CREDIT_NOTE

    # The document of "none" is voided in SIESA, then comes back.
    lost = source.items.pop(2)
    assert (await service.sync())["voided"] == 1
    assert (await repository.get_by_line(f"{tag}-none")).status == VOIDED
    source.items.append(lost)
    await service.sync()
    restored = await service.get((await repository.get_by_line(f"{tag}-none")).id)
    assert restored.status == PENDING and not restored.voided
    assert [event.event_type for event in restored.events] == ["detectada", "anulada", "reactivada"]


async def test_numbers_are_stored_as_written(world) -> None:
    service, repository, _, _, tag = world
    await service.sync()
    novelty = await repository.get_by_line(f"{tag}-up")

    await service.attach_credit_note(
        PERSON,
        novelty.id,
        number="NC 1708008745",
        amount="12000.5",
        upload=UploadedFile("a.pdf", PDF),
    )

    async with repository.database.session_factory() as session:
        stored = await session.scalar(
            select(CostCreditNoteModel).where(CostCreditNoteModel.supplier_nit == f"9{tag}")
        )
    assert stored.number == "NC 1708008745" and stored.original_filename == "a.pdf"
    assert (await service.get(novelty.id)).credit_note.amount == Decimal("12000.50")
