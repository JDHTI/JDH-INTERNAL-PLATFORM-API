"""The board of Listas principales: every main list in one table."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError
from app.modules.jobs.domain.entities import JobRun
from app.modules.jobs.domain.registry import OPEN_STATUSES, RunStatus
from app.modules.quotations.application.main_board import MainBoardService, PriceInput
from app.modules.quotations.domain.board import BoardFilter, BoardKey, BoardRow, WarehouseStock
from app.modules.quotations.domain.entities import ItemInfo, ListItem, PriceListInfo, StockRow
from app.modules.quotations.domain.pricing import MAIN_LIST_WAREHOUSES
from app.modules.quotations.domain.vocabulary import MAIN_LIST
from tests.modules.quotations.conftest import MAIN_APPROVER, MAIN_MANAGER, NIT, branch
from tests.modules.supplier_supports.conftest import person

TOLERANCE = Decimal("0.005")


def _floor(line) -> Decimal | None:
    """The highest cost at the minimum margin, in whole pesos up (BI, 2026-11-26)."""

    if line.cost is None or line.min_margin_pct is None or line.min_margin_pct >= 1:
        return None
    return (line.cost / (1 - line.min_margin_pct)).to_integral_value(rounding="ROUND_CEILING")


def _trend(line, trend: str) -> bool:
    """As the board's SQL: under the floor goes up, else down; without a current price, a
    cost or a minimum margin, no trend."""

    floor = _floor(line)
    if line.last_price is None or floor is None:
        return False
    return line.last_price < floor if trend == "sube" else line.last_price >= floor


def _chip(line, chip: str) -> bool:
    margin, target = line.current_margin_with_support_pct, line.target_margin_pct
    judged = line.last_price is not None and margin is not None and target is not None
    up = _trend(line, "sube")
    return {
        "sin_marcar": line.review_status == "sin_marcar" and line.load_id is None,
        "por_validar": line.review_status == "por_validar" and line.load_id is None,
        "validada": line.review_status == "validada" and line.load_id is None,
        "cargada": line.load_id is not None,
        "alza": up,
        "objetivo": judged and not up and target - TOLERANCE <= margin <= target + TOLERANCE,
        "encima": judged and not up and margin > target + TOLERANCE,
        "alerta": line.is_active
        and (line.below_target or line.cost is None or "precio_marcador" in line.problems),
    }[chip]


class FakeBoardRepository:
    """The board's reads over the in-memory quotations."""

    def __init__(self, quotations) -> None:
        self.quotations = quotations
        # What the sync stored: (on hand, available) by (update, SKU), and the stock by
        # warehouse (2026-11-26).
        self.stock: dict[tuple, tuple] = {}
        self.breakdown: dict[tuple, tuple[WarehouseStock, ...]] = {}

    async def set_stock(self, quotation_id, stock, breakdown=()):
        for key in [key for key in self.breakdown if key[0] == quotation_id]:
            del self.breakdown[key]
        for row in breakdown:
            if row.on_hand > 0:
                self.breakdown[(quotation_id, row.sku)] = (
                    *self.breakdown.get((quotation_id, row.sku), ()),
                    WarehouseStock(row.warehouse, row.warehouse_name, row.on_hand, row.available),
                )
        for line in self.quotations.items[quotation_id].lines:
            if not line.is_local:
                self.stock[(quotation_id, line.sku)] = stock.get(line.sku, (Decimal(0), Decimal(0)))

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
            chosen = set(board.warehouses)
            own = set(MAIN_LIST_WAREHOUSES.get(quotation.price_list_id, ()))
            if chosen and not chosen & own:
                continue
            for line in detail.lines:
                if board.q and not (
                    line.sku.startswith(board.q)
                    or board.q.lower() in (line.description or "").lower()
                ):
                    continue
                if with_chip and board.chip and not _chip(line, board.chip):
                    continue
                if board.trend and not _trend(line, board.trend):
                    continue
                if board.without_list_price and line.last_price is not None:
                    continue
                on_hand, available = self.stock.get((quotation.id, line.sku), (None, None))
                by_warehouse = self.breakdown.get((quotation.id, line.sku), ())
                if chosen and board.in_stock:
                    if not any(item.warehouse in chosen for item in by_warehouse):
                        continue  # in stock in a warehouse chosen (2026-11-26)
                elif board.in_stock and on_hand is not None and on_hand <= 0:
                    continue  # only what is in stock (2026-11-23)
                yield BoardRow(
                    quotation.id,
                    quotation.version,
                    quotation.price_list_id,
                    quotation.price_list_name,
                    line,
                    on_hand,
                    available,
                    by_warehouse,
                )

    async def rows(self, ids, board, page, size):
        rows = list(self._matching(ids, board))
        if board.sort == "existencia":
            # Most stock first, unread last (as NULLS LAST does).
            rows.sort(key=lambda row: (row.stock_on_hand is None, -(row.stock_on_hand or 0)))
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
    # Both are in stock in Occidente (the board only shows what is in stock, 2026-11-23).
    insights.stock_rows += [
        StockRow("2537", "OCC1", "PEREIRA PRINCIPAL", "002", Decimal(30), Decimal(30)),
        StockRow("26278", "OCC1", "PEREIRA PRINCIPAL", "002", Decimal(12), Decimal(10)),
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


async def test_prices_reviews_and_notes_reach_each_list(
    board, repository, collaborator, analyst
) -> None:
    await board.build(collaborator, "001")
    await board.build(collaborator, "002")
    rows = (await board.rows(collaborator, BoardFilter(), 1, 50)).page.rows
    keys = [BoardKey(row.quotation_id, row.line.sku) for row in rows]
    full_reads = repository.full_reads

    priced = await board.set_prices(
        collaborator,
        [
            PriceInput(keys[0].quotation_id, "2537", Decimal(4700)),
            PriceInput(keys[1].quotation_id, "2537", Decimal(3300)),
        ],
    )
    assert priced.done == 2 and priced.skipped == ()
    # A new price is ready to validate at once; the answer has the rows as they are now.
    assert [
        (row.price_list_id, row.line.new_price, row.line.review_status, row.line.reviewed_by)
        for row in priced.rows
    ] == [
        ("001", Decimal(4700), "por_validar", MAIN_MANAGER),
        ("002", Decimal(3300), "por_validar", MAIN_MANAGER),
    ]
    assert priced.rows[1].line.variation_pct is not None
    unmarked = await board.mark(collaborator, keys[:2], "sin_marcar")
    assert unmarked.done == 2 and {row.line.review_status for row in unmarked.rows} == {
        "sin_marcar"
    }
    marked = await board.mark(collaborator, keys, "por_validar")
    # The liquor has no new price: it cannot be marked.
    assert marked.done == 2 and [item.sku for item in marked.skipped] == ["26278"]
    with pytest.raises(ForbiddenError):
        await board.mark(collaborator, keys[:2], "validada")
    validated = await board.mark(analyst, keys[:2], "validada")
    assert validated.done == 2
    noted = await board.note(collaborator, keys[2].quotation_id, "26278", "Revisar el impuesto")
    assert noted.line.note == "Revisar el impuesto"
    # Every edit read and wrote only its rows, never a whole list.
    assert repository.full_reads == full_reads

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

    edit = await board.apply(
        other, [BoardKey(row.quotation_id, row.line.sku) for row in rows], "optimo"
    )

    # Another collaborator edits a list someone else built; the liquor has no cost.
    assert edit.done == 1
    assert [(item.sku, item.reason) for item in edit.skipped] == [
        ("26278", "Sin costo: no hay precio sugerido")
    ]
    after = {
        row.line.sku: row.line for row in (await board.rows(other, BoardFilter(), 1, 50)).page.rows
    }
    assert after["2537"].new_price == after["2537"].optimal_price
    cleared = await board.apply(
        other, [BoardKey(row.quotation_id, row.line.sku) for row in rows], "limpiar"
    )
    assert cleared.done == 2


async def test_a_price_moves_the_counts_of_its_list_without_reading_it_whole(
    board, repository, collaborator
) -> None:
    await board.build(collaborator, "002")
    rows = (await board.rows(collaborator, BoardFilter(), 1, 50)).page.rows
    update = rows[0].quotation_id
    before = repository.items[update].quotation

    await board.set_prices(collaborator, [PriceInput(update, "2537", Decimal(3300))])
    priced = repository.items[update].quotation
    cleared = await board.set_prices(collaborator, [PriceInput(update, "2537", None)])
    after = repository.items[update].quotation

    assert priced.changed_count == before.changed_count + 1
    assert priced.version == before.version + 1 and priced.line_count == before.line_count
    # Cleared, the row is unmarked and out of the count again.
    assert cleared.rows[0].line.review_status == "sin_marcar"
    assert cleared.rows[0].line.reviewed_by is None
    assert after.changed_count == before.changed_count


async def test_the_sync_stores_the_stock_and_the_board_sorts_by_it(
    board, insights, collaborator
) -> None:
    await board.build(collaborator, "002")
    await board.build(collaborator, "001")
    insights.calls.clear()

    result = await board.rows(collaborator, BoardFilter(sort="existencia", descending=True), 1, 50)

    first = result.page.rows[0]
    # 2537 has 80 in MDE1 (list 001); list 002's warehouses have 30 and 12.
    assert (first.price_list_id, first.line.sku, first.stock_on_hand) == ("001", "2537", 80)
    assert result.stock[(first.quotation_id, "2537")].on_hand == Decimal(80)
    assert [row.stock_on_hand for row in result.page.rows[1:]] == [30, 12]
    # The page does not read the warehouse: the sync stored the stock of every row.
    assert not [call for call in insights.calls if call[0] == "stock"]

    # A list not synced since the stock is stored is read page by page.
    board.repository.stock.clear()
    again = await board.rows(collaborator, BoardFilter(), 1, 50)
    assert again.stock_read and again.stock[(first.quotation_id, "2537")].on_hand == Decimal(80)
    # One read per list.
    assert [call[0] for call in insights.calls] == ["stock", "stock"]


async def test_the_board_breaks_the_stock_down_by_warehouse_and_filters_by_it(
    board, insights, collaborator
) -> None:
    """2026-11-26: each list reads its own warehouses (BI's mapping), stores the stock of
    each one, and the board filters by warehouse showing only the chosen ones' stock."""

    await board.build(collaborator, "002")
    await board.build(collaborator, "001")
    reads = {tuple(call[2]) for call in insights.calls if call[0] == "stock"}
    assert ("MDE1", "MDE3") in reads and ("OCC1", "OCC16") in reads

    every = await board.rows(collaborator, BoardFilter(), 1, 50)
    row = next(item for item in every.page.rows if item.price_list_id == "002")
    assert {item.warehouse for item in row.stock_by_warehouse} <= {"OCC1", "OCC16"}
    assert row.stock_by_warehouse

    chosen = row.stock_by_warehouse[0]
    only = await board.rows(collaborator, BoardFilter(warehouses=(chosen.warehouse,)), 1, 50)
    assert {item.price_list_id for item in only.page.rows} == {"002"}
    picked = next(item for item in only.page.rows if item.line.sku == row.line.sku)
    assert only.stock[(picked.quotation_id, picked.line.sku)].on_hand == chosen.on_hand

    overview = await board.overview(collaborator)
    # The warehouses of the configured lists, in list order, without repeats.
    assert overview.warehouses[:2] == ("MDE1", "MDE3")
    assert len(overview.warehouses) == len(set(overview.warehouses))
    assert next(item for item in overview.lists if item.code == "001").warehouses == (
        "MDE1",
        "MDE3",
    )
    with pytest.raises(InvalidInputError, match="bodega"):
        await board.rows(collaborator, BoardFilter(warehouses=("XYZ9",)), 1, 50)


async def test_the_trend_filter_is_checked_and_reaches_the_keys(board, collaborator) -> None:
    """``trend`` and ``without_list_price`` filter the rows and the keys; the SQL is checked
    on PostgreSQL in ``test_board_postgres``. «estable» is gone since 2026-11-26."""

    await board.build(collaborator, "002")
    for unknown in ("arriba", "estable"):
        with pytest.raises(InvalidInputError, match="Tendencia desconocida"):
            await board.rows(collaborator, BoardFilter(trend=unknown), 1, 50)
    every = await board.keys(collaborator, BoardFilter())
    rising = await board.keys(collaborator, BoardFilter(trend="sube"))
    falling = await board.keys(collaborator, BoardFilter(trend="baja"))
    # Up and down never overlap, and both are rows of the board.
    assert not set(rising) & set(falling) and set(rising) | set(falling) <= set(every)
    unpriced = await board.keys(collaborator, BoardFilter(without_list_price=True))
    assert not set(unpriced) & (set(rising) | set(falling))


async def test_the_board_hides_what_is_out_of_stock_and_small_rises(
    board, insights, collaborator
) -> None:
    """2026-11-23: a row the sync read at 0 is not on the board. 2026-11-26: a current price
    under the highest cost at the minimum margin goes up («alza» and «sube»), else down."""

    insights.stock_rows = [row for row in insights.stock_rows if row.sku != "26278"]
    await board.build(collaborator, "002")

    result = await board.rows(collaborator, BoardFilter(), 1, 50)

    assert [row.line.sku for row in result.page.rows] == ["2537"]
    assert result.page.counts["todos"] == 1
    line = replace(
        result.page.rows[0].line,
        cost=Decimal("820"),
        min_margin_pct=Decimal("0.18"),
        new_price=None,
    )
    # 820 / 0.82 = 1000: the floor price.
    assert _chip(replace(line, last_price=Decimal("999")), "alza")
    assert _trend(replace(line, last_price=Decimal("999")), "sube")
    assert _trend(replace(line, last_price=Decimal("1000")), "baja")
    assert not _chip(replace(line, last_price=Decimal("1000")), "alza")
    # The new price no longer decides: only the current one against the floor.
    assert _trend(replace(line, last_price=Decimal("999"), new_price=Decimal("2000")), "sube")
    # Without a cost there is no trend.
    assert not _trend(replace(line, cost=None, last_price=Decimal("1")), "sube")


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
