"""Item review, staging, loads and item detail of the list updates."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.quotations.application.service import LineChange
from app.modules.quotations.domain import pricing
from tests.modules.quotations.conftest import LIST_MANAGER, MAIN_APPROVER, MAIN_MANAGER, NIT
from tests.modules.supplier_supports.conftest import TODAY, person


@pytest.fixture
def lister():
    return person(LIST_MANAGER)


async def _priced(service, who, price_list_id="10U", kind="lista", price=4700):
    detail = await service.create_list_update(who, price_list_id=price_list_id, kind=kind)
    priced, _ = await service.update_lines(
        who, detail.quotation.id, detail.quotation.version, [LineChange("2537", Decimal(price))]
    )
    return priced


async def test_an_item_goes_to_validation_then_to_the_staging(
    service, review, lister, approver
) -> None:
    priced = await _priced(service, lister)
    quotation = priced.quotation

    marked, skipped = await review.mark(
        lister, quotation.id, quotation.version, ["2537", "26278"], "por_validar"
    )

    by_sku = {line.sku: line for line in marked.lines}
    assert by_sku["2537"].review_status == "por_validar"
    assert by_sku["2537"].reviewed_by == LIST_MANAGER
    # The placeholder has no new price: it cannot be marked.
    assert [(item.sku, item.reason) for item in skipped] == [("26278", "Sin precio nuevo")]
    assert marked.quotation.status == "borrador"
    # Only who validates validates.
    with pytest.raises(ForbiddenError, match="Solo quien valida"):
        await review.mark(lister, quotation.id, marked.quotation.version, ["2537"], "validada")

    validated, _ = await review.mark(
        approver, quotation.id, marked.quotation.version, ["2537"], "validada"
    )

    line = validated.lines[0]
    assert line.review_status == "validada" and line.is_staged
    staging = await review.staging(lister, "lista")
    assert [(group.price_list_id, len(group.items)) for group in staging] == [("10U", 1)]
    # Whoever edits cannot take it back out of validada.
    with pytest.raises(ForbiddenError):
        await review.mark(lister, quotation.id, validated.quotation.version, ["2537"], "sin_marcar")


async def test_a_new_price_sends_the_item_back_to_unmarked(
    service, review, lister, approver
) -> None:
    priced = await _priced(service, lister)
    quotation = priced.quotation
    validated, _ = await review.mark(
        approver, quotation.id, quotation.version, ["2537"], "validada"
    )

    repriced, _ = await service.update_lines(
        lister, quotation.id, validated.quotation.version, [LineChange("2537", Decimal(4800))]
    )

    line = repriced.lines[0]
    assert line.review_status == "sin_marcar" and line.reviewed_by is None
    assert await review.staging(lister, "lista") == []


async def test_the_same_sku_is_not_staged_twice_for_a_list(
    service, review, lister, approver
) -> None:
    first = await _priced(service, lister)
    second = await _priced(service, lister, price=4900)
    await review.mark(approver, first.quotation.id, first.quotation.version, ["2537"], "validada")

    with pytest.raises(InvalidInputError, match="otra actualización"):
        await review.mark(
            approver, second.quotation.id, second.quotation.version, ["2537"], "validada"
        )


async def test_the_staging_files_and_load(service, review, lister, approver, repository) -> None:
    priced = await _priced(service, lister)
    quotation = priced.quotation
    validated, _ = await review.mark(
        approver, quotation.id, quotation.version, ["2537"], "validada"
    )
    line = validated.lines[0]
    activation = TODAY + timedelta(days=2)

    with pytest.raises(ForbiddenError):
        await review.staging_file(lister, "lista", "10U", "txt", activation)
    flat = await review.staging_file(approver, "lista", "10U", "txt", activation)
    workbook = await review.staging_file(approver, "lista", "10U", "xlsx", activation)

    text = flat.content.decode("ascii")
    assert flat.filename == f"precios-lista10U-staging-{activation:%Y%m%d}.txt"
    assert "0126" in text and "2537" in text and "000000000004700.0000" in text
    assert workbook.filename.endswith(".xlsx") and workbook.content[:2] == b"PK"
    with pytest.raises(InvalidInputError, match="anterior a hoy"):
        await review.staging_file(approver, "lista", "10U", "txt", TODAY - timedelta(days=1))

    load = await review.load(approver, "lista", "10U", [line.id], activation)

    assert load.line_count == 1 and Decimal(load.rows[0]["price"]) == Decimal(4700)
    assert Decimal(load.rows[0]["previous_price"]) == Decimal(4568)
    assert await review.staging(lister, "lista") == []
    stored = repository.items[quotation.id]
    assert stored.lines[0].load_id == load.id and stored.lines[0].review_status == "validada"
    assert [item.id for item in await review.loads(lister, "lista")] == [load.id]
    # A loaded item keeps its state until its price changes.
    with pytest.raises(InvalidInputError, match="Ya se cargó"):
        await review.mark(approver, quotation.id, stored.quotation.version, ["2537"], "sin_marcar")
    with pytest.raises(NotFoundError):
        await review.staging_file(approver, "lista", "10U", "txt", activation)


async def test_a_load_with_items_that_left_the_staging_is_refused(
    service, review, lister, approver
) -> None:
    priced = await _priced(service, lister)
    quotation = priced.quotation
    validated, _ = await review.mark(
        approver, quotation.id, quotation.version, ["2537"], "validada"
    )
    line_id = validated.lines[0].id
    await service.update_lines(
        lister, quotation.id, validated.quotation.version, [LineChange("2537", Decimal(4800))]
    )

    with pytest.raises((InvalidInputError, NotFoundError, ConflictError)):
        await review.load(approver, "lista", "10U", [line_id], None)


async def test_the_main_lists_are_reviewed_by_their_own_validators(
    service, review, approver
) -> None:
    collaborator = person(MAIN_MANAGER)
    analyst = person(MAIN_APPROVER)
    priced = await _priced(service, collaborator, price_list_id="001", kind="principal")
    quotation = priced.quotation

    # BI's validator of Actualización de listas does not validate the main lists.
    with pytest.raises(ForbiddenError):
        await review.mark(approver, quotation.id, quotation.version, ["2537"], "validada")
    with pytest.raises(ForbiddenError):
        await review.mark(collaborator, quotation.id, quotation.version, ["2537"], "validada")
    validated, _ = await review.mark(analyst, quotation.id, quotation.version, ["2537"], "validada")

    assert validated.lines[0].review_status == "validada"
    staged = await review.staging(collaborator, "principal")
    assert [group.price_list_id for group in staged] == ["001"]
    # The main lists' staging is apart from Actualización de listas'.
    assert await review.staging(approver, "lista") == []


async def test_an_item_takes_an_observation(service, review, lister) -> None:
    priced = await _priced(service, lister)
    quotation = priced.quotation

    noted = await review.set_note(
        lister, quotation.id, quotation.version, "2537", "  Subió el proveedor   en octubre "
    )

    assert noted.lines[0].note == "Subió el proveedor en octubre"
    cleared = await review.set_note(lister, quotation.id, noted.quotation.version, "2537", "")
    assert cleared.lines[0].note is None
    with pytest.raises(InvalidInputError, match="500"):
        await review.set_note(lister, quotation.id, cleared.quotation.version, "2537", "x" * 501)


async def test_the_item_detail_reads_stock_history_and_sales(
    service, review, lister, insights
) -> None:
    detail = await service.create_list_update(lister, price_list_id="10U")

    result = await review.insight(lister, detail.quotation.id, "2537")

    # 10U bills from 003: that center's warehouses (2026-11-19); Medellín's does not count.
    assert result.warehouses == ("BTA9", "BTA8", "BTA20")
    assert [row.warehouse for row in result.stock] == ["BTA9", "BTA8"]
    assert result.history[-1].price == Decimal(4568) and result.history[-1].valid_to is None
    assert result.sales[0].price == Decimal(4400) and result.sales_scope == "lista"
    sales_call = next(call for call in insights.calls if call[0] == "sales")
    assert sales_call[3] == ("1-900315138-001", "1-800111222-001")

    insights.sales_down = True
    slow = await review.insight(lister, detail.quotation.id, "2537")
    assert slow.sales == () and not slow.sales_read and slow.stock

    warehouses, totals = await review.stock_totals(lister, detail.quotation.id)
    assert totals["2537"].on_hand == Decimal(150) and totals["2537"].available == Decimal(130)
    assert totals["2537"].warehouses == 2 and warehouses == ("BTA9", "BTA8", "BTA20")


async def test_a_quotation_has_no_item_review(service, review, advisor) -> None:
    detail = await service.create(advisor, NIT, 12)

    with pytest.raises(InvalidInputError, match="por ítem"):
        await review.mark(
            advisor, detail.quotation.id, detail.quotation.version, ["2537"], "por_validar"
        )


def test_the_stock_warehouses_of_a_list() -> None:
    # Without a center: Nacional (the 17 main ones), whatever the list.
    assert pricing.stock_warehouses("10U", ()) == pricing.GENERAL_WAREHOUSES
    assert pricing.stock_warehouses("1", ()) == pricing.GENERAL_WAREHOUSES
    assert pricing.stock_scope("77X", ()).national
    # With centers (2026-11-19): the sellable warehouses of each chosen center, whatever the
    # list; a center without a row is read by its own warehouses in SIESA.
    assert pricing.stock_warehouses("77X", ("004",)) == ("CTG1", "CTG15", "CTG14")
    assert pricing.stock_warehouses("1", ("001", "002")) == (
        "MDE1",
        "MDE3",
        "MDE4",
        "OCC1",
        "OCC16",
        "OCC14",
    )
    urab = pricing.stock_scope("10U", ("003", "007"))
    assert urab.warehouses == ("BTA9", "BTA8", "BTA20") and urab.centers == ("007",)
    assert not urab.national
    # Every main warehouse belongs to one center.
    assert sorted(
        code for codes in pricing.CENTER_STOCK_WAREHOUSES.values() for code in codes
    ) == sorted(pricing.GENERAL_WAREHOUSES)
