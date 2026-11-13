"""The board of Listas principales: every main list in one table."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError
from app.modules.jobs.domain.entities import JobRun
from app.modules.jobs.domain.registry import OPEN_STATUSES, RunStatus
from app.modules.quotations.application.main_board import MainBoardService, PriceInput
from app.modules.quotations.domain.board import BoardFilter, BoardKey, BoardRow
from app.modules.quotations.domain.entities import ItemInfo, ListItem, PriceListInfo
from app.modules.quotations.domain.vocabulary import MAIN_LIST
from tests.modules.quotations.conftest import MAIN_APPROVER, MAIN_MANAGER, NIT, branch
from tests.modules.supplier_supports.conftest import person

TOLERANCE = Decimal("0.005")


def _chip(line, chip: str) -> bool:
    margin, target = line.current_margin_with_support_pct, line.target_margin_pct
    judged = line.last_price is not None and margin is not None and target is not None
    return {
        "sin_marcar": line.review_status == "sin_marcar" and line.load_id is None,
        "por_validar": line.review_status == "por_validar" and line.load_id is None,
        "validada": line.review_status == "validada" and line.load_id is None,
        "cargada": line.load_id is not None,
        "alza": judged and margin < target - TOLERANCE,
        "objetivo": judged and target - TOLERANCE <= margin <= target + TOLERANCE,
        "encima": judged and margin > target + TOLERANCE,
        "alerta": line.is_active
        and (line.below_target or line.cost is None or "precio_marcador" in line.problems),
    }[chip]


class FakeBoardRepository:
    """The board's reads over the in-memory quotations."""

    def __init__(self, quotations) -> None:
        self.quotations = quotations

    async def current_updates(self, kind):
        latest = {}
        for detail in self.quotations.items.values():
            quotation = detail.quotation
            if quotation.kind != kind:
                continue
            seen = latest.get(quotation.price_list_id)
            if seen is None or quotation.created_at > seen.created_at:
                latest[quotation.price_list_id] = quotation
        return [latest[code] for code in sorted(latest)]

    def _matching(self, ids, board: BoardFilter, with_chip=True):
        for quotation_id in ids:
            detail = self.quotations.items[quotation_id]
            quotation = detail.quotation
            if board.lists and quotation.price_list_id not in board.lists:
                continue
            for line in detail.lines:
                if board.q and not (
                    line.sku.startswith(board.q)
                    or board.q.lower() in (line.description or "").lower()
                ):
                    continue
                if with_chip and board.chip and not _chip(line, board.chip):
                    continue
                yield BoardRow(
                    quotation.id,
                    quotation.version,
                    quotation.price_list_id,
                    quotation.price_list_name,
                    line,
                )

    async def rows(self, ids, board, page, size):
        rows = list(self._matching(ids, board))
        everything = list(self._matching(ids, board, with_chip=False))
        counts = {
            chip: sum(1 for row in everything if _chip(row.line, chip))
            for chip in (
                "sin_marcar",
                "por_validar",
                "validada",
                "cargada",
                "alza",
                "objetivo",
                "encima",
                "alerta",
            )
        }
        counts["todos"] = len(everything)
        return rows[(page - 1) * size : page * size], len(rows), counts

    async def keys(self, ids, board):
        return [BoardKey(row.quotation_id, row.line.sku) for row in self._matching(ids, board)]

    async def list_counts(self, ids):
        result = {}
        for quotation_id in ids:
            counts = {}
            for line in self.quotations.items[quotation_id].lines:
                state = "cargada" if line.load_id else line.review_status
                counts[state] = counts.get(state, 0) + 1
            result[quotation_id] = counts
        return result

    async def options(self, ids):
        lines = [line for item in ids for line in self.quotations.items[item].lines]
        return (
            sorted({line.supplier_name for line in lines if line.supplier_name}),
            sorted({line.category for line in lines if line.category}),
        )


@pytest.fixture
def board(service, review, repository, insights, warehouse) -> MainBoardService:
    # A second main list, 002, with the same abrasive at another price and a liquor.
    warehouse.lists["002"] = PriceListInfo("002", "GENERAL OCCIDENTE", True, 1)
    warehouse.list_branch_rows["002"] = [
        branch(price_list_id="002", billing_center_code="002", nit=NIT, customer_key="k-002")
    ]
    warehouse.list_item_rows["002"] = [
        ListItem(ItemInfo("2537", "Abrasivo", "UNID", Decimal(19), "359"), Decimal("3100")),
        ListItem(
            ItemInfo("26278", "Ginebra Selva x700ml", "UNID", Decimal(5), "359"), Decimal(90000)
        ),
    ]
    return MainBoardService(
        quotations=service,
        review=review,
        repository=FakeBoardRepository(repository),
        insights=insights,
    )


@pytest.fixture
def collaborator():
    return person(MAIN_MANAGER)


@pytest.fixture
def analyst():
    return person(MAIN_APPROVER)


async def test_every_main_list_is_on_the_board_and_built_one_by_one(board, collaborator) -> None:
    before = await board.overview(collaborator)

    assert [item.code for item in before.lists][:3] == ["1", "001", "002"]
    assert all(item.quotation_id is None for item in before.lists)

    built = await board.build(collaborator, "001")
    await board.build(collaborator, "002")
    after = await board.overview(collaborator)

    assert built.quotation_id is not None and built.line_count == 1
    by_code = {item.code: item for item in after.lists}
    assert by_code["002"].line_count == 2 and by_code["002"].counts == {"sin_marcar": 2}
    assert by_code["003"].quotation_id is None
    with pytest.raises(InvalidInputError, match="no es una lista principal"):
        await board.build(collaborator, "10U")
    # Building again refreshes the same update, it does not open a second one.
    again = await board.build(collaborator, "001")
    assert again.quotation_id == built.quotation_id


async def test_the_board_reads_every_list_in_one_table_and_filters_by_list(
    board, collaborator
) -> None:
    await board.build(collaborator, "001")
    await board.build(collaborator, "002")

    everything = await board.rows(collaborator, BoardFilter(), 1, 50)
    only = await board.rows(collaborator, BoardFilter(lists=("002",)), 1, 50)

    assert [(row.price_list_id, row.line.sku) for row in everything.page.rows] == [
        ("001", "2537"),
        ("002", "2537"),
        ("002", "26278"),
    ]
    assert everything.page.counts["sin_marcar"] == 3
    assert [row.line.sku for row in only.page.rows] == ["2537", "26278"]
    with pytest.raises(InvalidInputError):
        await board.rows(collaborator, BoardFilter(lists=("10U",)), 1, 50)
    with pytest.raises(ForbiddenError):
        await board.rows(person("otro@jdh.com.co"), BoardFilter(), 1, 50)


async def test_prices_reviews_and_notes_reach_each_list(board, collaborator, analyst) -> None:
    await board.build(collaborator, "001")
    await board.build(collaborator, "002")
    rows = (await board.rows(collaborator, BoardFilter(), 1, 50)).page.rows
    keys = [BoardKey(row.quotation_id, row.line.sku) for row in rows]

    written, skipped = await board.set_prices(
        collaborator,
        [
            PriceInput(keys[0].quotation_id, "2537", Decimal(4700)),
            PriceInput(keys[1].quotation_id, "2537", Decimal(3300)),
        ],
    )
    assert written == 2 and skipped == ()
    marked, skipped = await board.mark(collaborator, keys, "por_validar")
    # The liquor has no new price: it cannot be marked.
    assert marked == 2 and [item.sku for item in skipped] == ["26278"]
    with pytest.raises(ForbiddenError):
        await board.mark(collaborator, keys[:2], "validada")
    validated, _ = await board.mark(analyst, keys[:2], "validada")
    assert validated == 2
    noted = await board.note(collaborator, keys[2].quotation_id, "26278", "Revisar el impuesto")
    assert noted.line.note == "Revisar el impuesto"

    after = await board.rows(collaborator, BoardFilter(chip="validada"), 1, 50)
    assert {(row.price_list_id, row.line.new_price) for row in after.page.rows} == {
        ("001", Decimal(4700)),
        ("002", Decimal(3300)),
    }


async def test_the_board_is_shared_by_every_collaborator(board, service, collaborator) -> None:
    await board.build(collaborator, "002")
    service.main_managers.emails.add("otro.colaborador@jdh.com.co")
    other = person("otro.colaborador@jdh.com.co")
    rows = (await board.rows(other, BoardFilter(), 1, 50)).page.rows

    written, skipped = await board.apply(
        other, [BoardKey(row.quotation_id, row.line.sku) for row in rows], "optimo"
    )

    # Another collaborator edits a list someone else built; the liquor has no cost.
    assert written == 1
    assert [(item.sku, item.reason) for item in skipped] == [
        ("26278", "Sin costo: no hay precio sugerido")
    ]
    after = {
        row.line.sku: row.line for row in (await board.rows(other, BoardFilter(), 1, 50)).page.rows
    }
    assert after["2537"].new_price == after["2537"].optimal_price
    cleared, _ = await board.apply(
        other, [BoardKey(row.quotation_id, row.line.sku) for row in rows], "limpiar"
    )
    assert cleared == 2


class FakeRuns:
    def __init__(self) -> None:
        self.runs: list[JobRun] = []

    async def queue(self, job, trigger, now, *, triggered_by=None):
        if any(run.job == job and run.status in OPEN_STATUSES for run in self.runs):
            raise ConflictError("abierta")
        run = JobRun(uuid4(), job, trigger, RunStatus.QUEUED, now, triggered_by=triggered_by)
        self.runs.append(run)
        return run

    async def latest(self, job):
        return next((run for run in reversed(self.runs) if run.job == job), None)


async def test_the_sync_is_a_background_job(board, collaborator) -> None:
    requested: list = []
    board.runs, board.request_run, board.jobs_enabled = FakeRuns(), requested.append, True

    run = await board.sync(collaborator)

    assert run.job == "main-lists-sync" and run.triggered_by == MAIN_MANAGER
    assert requested == [run.id]
    with pytest.raises(ConflictError, match="en cola o en curso"):
        await board.sync(collaborator)
    assert (await board.overview(collaborator)).sync == run
    board.jobs_enabled = False
    with pytest.raises(ConflictError, match="JOBS_ENABLED"):
        await board.sync(collaborator)
    with pytest.raises(ForbiddenError):
        await board.sync(person("otro@jdh.com.co"))


async def test_the_job_builds_every_list_and_one_failure_does_not_stop_it(
    board, service, warehouse
) -> None:
    # Only 001 and 002 have prices in the fake warehouse; the other main lists do not exist.
    result = await board.sync_all()

    by_code = {item["code"]: item for item in result["lists"]}
    assert by_code["001"]["status"] == "ok" and by_code["002"]["line_count"] == 2
    assert by_code["003"]["status"] == "error" and "003" in by_code["003"]["error"]
    assert any(error.startswith("003:") for error in result["errors"])
    # The last sales may take longer in the background.
    assert service.list_sales_timeout == 180.0
    current = await board.repository.current_updates("principal")
    assert {item.price_list_id for item in current} == {"001", "002"}
    assert all(item.created_by == "sincronizacion.listas@plataforma.jdh" for item in current)


async def test_select_every_row_of_a_filter(board, collaborator) -> None:
    await board.build(collaborator, "001")
    await board.build(collaborator, "002")

    keys = await board.keys(collaborator, BoardFilter(q="ginebra"))

    assert [key.sku for key in keys] == ["26278"]
    assert MAIN_LIST == "principal"
