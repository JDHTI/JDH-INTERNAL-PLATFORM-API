"""The entries analysis and the novelties: reasons, credit notes and the daily job."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.costs.application.access import CostAccess
from app.modules.costs.application.entries import EntryAnalysisService, EntryCache
from app.modules.costs.application.novelties import NoveltyService, UploadedFile
from app.modules.costs.domain.entries import EntryFilters
from app.modules.costs.domain.novelties import (
    AWAITING_CREDIT_NOTE,
    EVENT_RESTORED,
    EVENT_VOIDED,
    NO_LONGER,
    PENDING,
    RESOLVED,
    VOIDED,
    NoveltyFilters,
)
from app.modules.identity.domain.principal import Principal
from tests.modules.costs.fakes import (
    NOW,
    TODAY,
    FakeGuard,
    FakeNoveltyRepository,
    FakePolicy,
    FakeSource,
    FakeStorage,
    line,
)

D = Decimal
PERSON = Principal(uuid4(), "compras@jdh.com.co", "Compras", False)
PDF = b"%PDF-1.7 nota"
REASONS = (("INCREMENTO NO ANUNCIADO", True), ("NEGOCIACION ESPECIAL", False), ("OBSEQUIO", False))


def access(entries: bool = True, novelties: bool = True) -> CostAccess:
    return CostAccess(
        entries=FakePolicy(manage=entries),
        novelties=FakePolicy(manage=novelties),
        increases=FakePolicy(),
        ledger=FakePolicy(),
    )


def entry_service(source, repository, clock=None) -> EntryAnalysisService:
    return EntryAnalysisService(
        source,
        repository,
        threshold=D("0.001"),
        max_range_days=200,
        cache_seconds=600,
        cache=EntryCache(),
        today=lambda: TODAY,
        monotonic=clock or (lambda: 0.0),
    )


def novelty_service(source, repository, *, can_entries=True, can_novelties=True, storage=None):
    return NoveltyService(
        repository,
        source,
        access(can_entries, can_novelties),
        storage or FakeStorage(),
        FakeGuard(),
        threshold=D("0.001"),
        sync_days=45,
        start_date=date(2026, 11, 1),
        today=lambda: TODAY,
        clock=lambda: NOW,
    )


def sample_source() -> FakeSource:
    return FakeSource(
        line("ok", price="50000", quotation="50000"),
        line("up", price="52500", quotation="50000", sku="2548", supplier="70 - BRINSA S.A."),
        line("none", price="1000", quotation=None, sku="7053", document="EA-1"),
        line("old", day=date(2026, 10, 1), price="9", quotation="1"),
    )


# --- Entries analysis ---


async def test_the_period_is_the_last_30_days_and_counts_cover_the_search() -> None:
    source = sample_source()
    service = entry_service(source, FakeNoveltyRepository())

    result = await service.page(
        since=None,
        until=None,
        filters=EntryFilters(),
        sort="date",
        descending=True,
        page=1,
        size=50,
    )

    assert (result.since, result.until) == (TODAY - timedelta(days=29), TODAY)
    assert {row.line.line_key for row in result.items} == {"ok", "up", "none"}
    assert result.counts == {
        "ok": 1,
        "novedad": 1,
        "sin_cotizacion": 1,
        "total": 3,
        "with_reason": 0,
    }


async def test_only_novelties_keeps_the_counts_of_the_whole_search() -> None:
    service = entry_service(sample_source(), FakeNoveltyRepository())

    result = await service.page(
        since=date(2026, 11, 1),
        until=TODAY,
        filters=EntryFilters(novelty_only=True),
        sort="difference_pct",
        descending=True,
        page=1,
        size=50,
    )

    # The largest difference first; a line without quotation always last.
    assert [row.line.line_key for row in result.items] == ["up", "none"]
    assert result.total == 2 and result.counts["ok"] == 1


async def test_filters_by_supplier_center_and_text() -> None:
    service = entry_service(sample_source(), FakeNoveltyRepository())

    async def keys(**filters) -> set[str]:
        result = await service.page(
            since=date(2026, 11, 1),
            until=TODAY,
            filters=EntryFilters(**filters),
            sort="date",
            descending=False,
            page=1,
            size=50,
        )
        return {row.line.line_key for row in result.items}

    assert await keys(supplier="70") == {"up"}
    assert await keys(co="3") == {"ok", "up", "none"}  # "3" and "003" are one center
    assert await keys(q="ea-1") == {"none"}
    assert await keys(q="25") == {"up"}  # a reference prefix
    assert await keys(status="sin_cotizacion") == {"none"}


async def test_the_period_is_read_once_while_it_is_kept() -> None:
    source = sample_source()
    now = [0.0]
    service = entry_service(source, FakeNoveltyRepository(), clock=lambda: now[0])
    args = dict(
        since=date(2026, 11, 1), until=TODAY, filters=EntryFilters(), sort="date", descending=True
    )

    await service.page(**args, page=1, size=1)
    await service.page(**args, page=2, size=1)
    now[0] = 601.0
    await service.page(**args, page=1, size=1)

    assert len(source.reads) == 2


async def test_previous_prices_are_worked_out_when_the_warehouse_lacks_them() -> None:
    source = FakeSource(
        line("a", day=date(2026, 11, 2), price="10"),
        line("b", day=date(2026, 11, 3), price="11"),
        previous=False,
    )
    service = entry_service(source, FakeNoveltyRepository())

    result = await service.page(
        since=date(2026, 11, 1),
        until=TODAY,
        filters=EntryFilters(),
        sort="date",
        descending=False,
        page=1,
        size=5,
    )

    assert not result.previous_from_warehouse
    assert [row.line.previous_price for row in result.items] == [None, D("10")]


@pytest.mark.parametrize(
    ("since", "until", "message"),
    [
        (date(2026, 11, 10), date(2026, 11, 1), "posterior"),
        (date(2026, 1, 1), date(2026, 11, 1), "200 días"),
    ],
)
async def test_bad_periods_are_refused(since, until, message) -> None:
    service = entry_service(sample_source(), FakeNoveltyRepository())

    with pytest.raises(InvalidInputError, match=message):
        await service.page(
            since=since,
            until=until,
            filters=EntryFilters(),
            sort="date",
            descending=True,
            page=1,
            size=5,
        )


# --- Reasons ---


async def test_a_reason_on_a_line_keeps_it_as_a_novelty_and_shows_in_the_analysis() -> None:
    source = sample_source()
    repository = FakeNoveltyRepository(REASONS)
    novelties = novelty_service(source, repository)
    increase = repository.reason("INCREMENTO NO ANUNCIADO")

    novelty = await novelties.set_reason_for_line(PERSON, "up", increase.id)

    assert novelty.status == AWAITING_CREDIT_NOTE and novelty.reason.id == increase.id
    assert [event.event_type for event in novelty.events] == ["detectada", "motivo"]
    analysis = await entry_service(source, repository).page(
        since=date(2026, 11, 1),
        until=TODAY,
        filters=EntryFilters(reason_id=increase.id),
        sort="date",
        descending=True,
        page=1,
        size=5,
    )
    assert [row.line.line_key for row in analysis.items] == ["up"]
    assert analysis.items[0].novelty.reason_name == "INCREMENTO NO ANUNCIADO"
    assert analysis.counts["with_reason"] == 1


async def test_a_reason_without_credit_note_resolves_and_none_reopens() -> None:
    repository = FakeNoveltyRepository(REASONS)
    novelties = novelty_service(sample_source(), repository)
    deal = repository.reason("NEGOCIACION ESPECIAL")

    novelty = await novelties.set_reason_for_line(PERSON, "up", deal.id)
    assert novelty.status == RESOLVED

    novelty = await novelties.set_reason(PERSON, novelty.id, None)
    assert novelty.status == PENDING and novelty.reason is None


async def test_who_reads_only_cannot_give_reasons() -> None:
    repository = FakeNoveltyRepository(REASONS)
    novelties = novelty_service(sample_source(), repository, can_entries=False, can_novelties=False)

    with pytest.raises(ForbiddenError):
        await novelties.set_reason_for_line(PERSON, "up", repository.reason("OBSEQUIO").id)


async def test_the_entries_manager_alone_may_give_reasons() -> None:
    repository = FakeNoveltyRepository(REASONS)
    novelties = novelty_service(sample_source(), repository, can_novelties=False)

    novelty = await novelties.set_reason_for_line(PERSON, "up", repository.reason("OBSEQUIO").id)

    assert novelty.reason.name == "OBSEQUIO"


async def test_unknown_lines_and_inactive_reasons_are_refused() -> None:
    repository = FakeNoveltyRepository(REASONS)
    novelties = novelty_service(sample_source(), repository)
    gift = repository.reason("OBSEQUIO")
    await repository.update_reason(gift.id, name=None, requires_credit_note=None, is_active=False)

    with pytest.raises(NotFoundError):
        await novelties.set_reason_for_line(
            PERSON, "nope", repository.reason("NEGOCIACION ESPECIAL").id
        )
    with pytest.raises(InvalidInputError, match="inactivo"):
        await novelties.set_reason_for_line(PERSON, "up", gift.id)


async def test_changing_whether_a_reason_needs_a_credit_note_moves_its_novelties() -> None:
    repository = FakeNoveltyRepository(REASONS)
    novelties = novelty_service(sample_source(), repository)
    deal = repository.reason("NEGOCIACION ESPECIAL")
    novelty = await novelties.set_reason_for_line(PERSON, "up", deal.id)

    await novelties.update_reason(PERSON, deal.id, requires_credit_note=True)

    assert (await novelties.get(novelty.id)).status == AWAITING_CREDIT_NOTE
    with pytest.raises(ForbiddenError):
        await novelty_service(sample_source(), repository, can_novelties=False).update_reason(
            PERSON, deal.id, name="X"
        )


# --- Credit notes ---


async def _awaiting(repository, novelties):
    return await novelties.set_reason_for_line(
        PERSON, "up", repository.reason("INCREMENTO NO ANUNCIADO").id
    )


async def test_a_credit_note_resolves_the_novelty_and_keeps_its_file() -> None:
    repository = FakeNoveltyRepository(REASONS)
    storage = FakeStorage()
    novelties = novelty_service(sample_source(), repository, storage=storage)
    novelty = await _awaiting(repository, novelties)

    novelty = await novelties.attach_credit_note(
        PERSON,
        novelty.id,
        number=" NC 1708008745 ",
        amount="12000,5",
        upload=UploadedFile("nc.pdf", PDF),
    )

    assert novelty.status == RESOLVED
    assert (novelty.credit_note.number, novelty.credit_note.amount) == (
        "NC 1708008745",
        D("12000.50"),
    )
    assert novelty.credit_note.supplier_nit == "860002518"
    note, content = await novelties.credit_note_file(novelty.id)
    assert content == PDF and note.storage_key in storage.objects


async def test_the_same_number_of_the_same_supplier_reuses_the_file() -> None:
    source = sample_source()
    source.items.append(line("up2", price="52500", quotation="50000", sku="2548"))
    repository = FakeNoveltyRepository(REASONS)
    storage = FakeStorage()
    novelties = novelty_service(source, repository, storage=storage)
    reason = repository.reason("INCREMENTO NO ANUNCIADO").id
    first = await novelties.set_reason_for_line(PERSON, "up", reason)
    second = await novelties.set_reason_for_line(PERSON, "up2", reason)

    await novelties.attach_credit_note(
        PERSON, first.id, number="NC-9", amount="100", upload=UploadedFile("a.pdf", PDF)
    )
    shared = await novelties.attach_credit_note(
        PERSON, second.id, number="nc-9", amount="50", upload=None
    )

    assert shared.credit_note.number == "NC-9" and shared.credit_note.amount == D("50")
    assert len(storage.objects) == 1
    # Taking it off one keeps the file; off the last one, the file goes.
    await novelties.detach_credit_note(PERSON, first.id)
    assert len(storage.objects) == 1
    back = await novelties.detach_credit_note(PERSON, second.id)
    assert storage.objects == {} and back.status == AWAITING_CREDIT_NOTE


@pytest.mark.parametrize(
    ("number", "amount", "upload", "error"),
    [
        ("", "100", UploadedFile("a.pdf", PDF), InvalidInputError),
        ("NC-1", "0", UploadedFile("a.pdf", PDF), InvalidInputError),
        ("NC-1", "doce mil", UploadedFile("a.pdf", PDF), InvalidInputError),
        ("NC-1", "100", None, InvalidInputError),  # a new number needs its file
        ("NC-1", "100", UploadedFile("a.exe", b"MZ"), InvalidInputError),
        ("NC-1", "100", UploadedFile("a.pdf", b"not a pdf"), InvalidInputError),
        ("x" * 61, "100", UploadedFile("a.pdf", PDF), InvalidInputError),
    ],
)
async def test_bad_credit_notes_are_refused(number, amount, upload, error) -> None:
    repository = FakeNoveltyRepository(REASONS)
    storage = FakeStorage()
    novelties = novelty_service(sample_source(), repository, storage=storage)
    novelty = await _awaiting(repository, novelties)

    with pytest.raises(error):
        await novelties.attach_credit_note(
            PERSON, novelty.id, number=number, amount=amount, upload=upload
        )
    assert storage.objects == {}


async def test_one_credit_note_per_novelty_and_only_novelty_managers() -> None:
    repository = FakeNoveltyRepository(REASONS)
    novelties = novelty_service(sample_source(), repository)
    novelty = await _awaiting(repository, novelties)
    await novelties.attach_credit_note(
        PERSON, novelty.id, number="NC-1", amount="1", upload=UploadedFile("a.pdf", PDF)
    )

    with pytest.raises(ConflictError):
        await novelties.attach_credit_note(
            PERSON, novelty.id, number="NC-2", amount="1", upload=UploadedFile("b.pdf", PDF)
        )
    with pytest.raises(ForbiddenError):
        await novelty_service(sample_source(), repository, can_novelties=False).detach_credit_note(
            PERSON, novelty.id
        )


# --- The daily job ---


async def test_the_job_keeps_new_novelties_only() -> None:
    repository = FakeNoveltyRepository(REASONS)
    novelties = novelty_service(sample_source(), repository)

    report = await novelties.sync()

    kept = {row["line"].line_key: row["status"] for row in repository.rows.values()}
    assert kept == {"up": PENDING, "none": PENDING}  # the ok line and the October one stay out
    assert report["created"] == 2 and report["lines_read"] == 3
    assert (report["since"], report["until"]) == ("2026-11-01", "2026-11-23")


async def test_the_job_voids_what_the_warehouse_lost_and_restores_it() -> None:
    source = sample_source()
    repository = FakeNoveltyRepository(REASONS)
    novelties = novelty_service(source, repository)
    await novelties.sync()
    lost = source.items.pop(1)  # "up": its document was voided

    report = await novelties.sync()
    novelty = await repository.get_by_line("up")
    assert report["voided"] == 1 and novelty.status == VOIDED and novelty.voided

    source.items.append(lost)
    report = await novelties.sync()
    novelty = await novelties.get((await repository.get_by_line("up")).id)
    assert report["restored"] == 1 and novelty.status == PENDING
    assert [event.event_type for event in novelty.events][-2:] == [EVENT_VOIDED, EVENT_RESTORED]


async def test_an_empty_warehouse_read_voids_nothing() -> None:
    source = sample_source()
    repository = FakeNoveltyRepository(REASONS)
    novelties = novelty_service(source, repository)
    await novelties.sync()
    source.items.clear()

    report = await novelties.sync()

    assert report["voided"] == 0
    assert all(not row["voided"] for row in repository.rows.values())


async def test_a_quotation_dated_back_resolves_a_pending_novelty_without_reason() -> None:
    source = sample_source()
    repository = FakeNoveltyRepository(REASONS)
    novelties = novelty_service(source, repository)
    await novelties.sync()
    source.items[2] = line("none", price="1000", quotation="1000", sku="7053", document="EA-1")

    await novelties.sync()

    novelty = await repository.get_by_line("none")
    assert novelty.kind == NO_LONGER and novelty.status == RESOLVED


async def test_the_novelties_page_validates_and_filters() -> None:
    repository = FakeNoveltyRepository(REASONS)
    novelties = novelty_service(sample_source(), repository)
    await novelties.sync()

    page = await novelties.page(NoveltyFilters(status=PENDING, supplier="70"), "date", True, 1, 50)

    assert [item.line.line_key for item in page.items] == ["up"]
    assert page.counts["pendiente"] == 1 and page.counts["total"] == 1
    with pytest.raises(InvalidInputError):
        await novelties.page(NoveltyFilters(status="cerrada"), "date", True, 1, 50)
