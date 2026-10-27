"""Use cases of the retained orders validator over in-memory ports."""

from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.order_validation.domain.entities import ProductInfo
from tests.modules.order_validation.conftest import agreement
from tests.modules.order_validation.test_order_file import HEADER, ROW, workbook
from tests.modules.supplier_supports.conftest import person

D = Decimal


def report() -> bytes:
    """Two orders: PV-1 has one line of 359, PV-2 one of 359 and one of 233."""

    def row(document: str, sku: int, margin: float, supplier: str) -> list:
        values = list(ROW)
        values[2], values[9], values[13], values[14] = document, sku, margin, supplier
        return values

    return workbook(
        HEADER,
        row("PV-1", 18294, 10.0, "359 - LOCERIA COLOMBIANA S.A."),
        row("PV-2", 28703, 10.0, "359 - LOCERIA COLOMBIANA S.A."),
        row("PV-2", 1128, 5.0, "233 - FAMILIA SANCELA"),
    )


async def test_upload_validates_every_line_and_counts_orders(service, source, manager) -> None:
    source.items.append(agreement(supplier_value=D("0.05"), target_margin_pct=D("0.14")))

    batch = await service.upload(manager, "Pedidos Retenidos.xlsx", report())

    assert batch.file_name == "Pedidos Retenidos.xlsx" and batch.uploaded_by == manager.email
    assert (batch.line_count, batch.order_count) == (3, 2)
    # Both 359 lines comply; the 233 line has no agreement, so PV-2 stays pending.
    assert (batch.ready_line_count, batch.ready_order_count) == (2, 1)
    assert batch.pending_order_count == 1


async def test_orders_group_lines_and_filter(service, source, manager) -> None:
    source.items.append(agreement(supplier_value=D("0.05"), target_margin_pct=D("0.14")))
    batch = await service.upload(manager, "p.xlsx", report())

    page = await service.orders(batch.id, sort="file")
    assert [order.document for order in page.items] == ["PV-1", "PV-2"]
    assert [order.status for order in page.items] == ["listo_para_liberar", "pendiente_validacion"]
    assert page.counts == {"listo_para_liberar": 1, "pendiente_validacion": 1}
    pv2 = page.items[1]
    assert len(pv2.lines) == 2 and pv2.ready_line_count == 1
    assert pv2.total_amount == D("118628") * 24 * 2

    pending = await service.orders(batch.id, status="pendiente_validacion")
    assert [order.document for order in pending.items] == ["PV-2"]
    assert pending.counts == page.counts
    assert (await service.orders(batch.id, reason="sin_acuerdo")).total == 1
    assert (await service.orders(batch.id, q="familia")).total == 1
    assert (await service.orders(batch.id, q="pv-1")).items[0].document == "PV-1"


async def test_revalidate_uses_the_supports_as_they_are_now(service, source, manager) -> None:
    batch = await service.upload(manager, "p.xlsx", report())
    assert batch.ready_order_count == 0

    source.items.append(agreement(supplier_value=D("0.05"), target_margin_pct=D("0.14")))
    source.catalog["1128"] = ProductInfo("1128", {"margin_list": D("0.05")})
    again = await service.revalidate(manager, batch.id)

    assert (again.ready_line_count, again.ready_order_count) == (3, 2)
    reasons = [
        line.result.reason
        for order in (await service.orders(batch.id, sort="file")).items
        for line in order.lines
    ]
    assert reasons == ["cumple_con_apoyo", "cumple_con_apoyo", "cumple_margen_lista"]


async def test_only_managers_upload_and_revalidate(service, manager) -> None:
    outsider = person("otro@jdh.com.co")
    batch = await service.upload(manager, "p.xlsx", report())

    with pytest.raises(ForbiddenError):
        await service.upload(outsider, "p.xlsx", report())
    with pytest.raises(ForbiddenError):
        await service.revalidate(outsider, batch.id)
    assert await service.can_manage(outsider) is False
    assert await service.can_manage(manager) is True
    # Reading is open to everyone signed in.
    assert (await service.list_batches()).total == 1


async def test_upload_limits(service, manager) -> None:
    with pytest.raises(InvalidInputError, match="vacío"):
        await service.upload(manager, "p.xlsx", b"")
    with pytest.raises(InvalidInputError, match="máximo de 1 MB"):
        await service.upload(manager, "p.xlsx", b"x" * (1024 * 1024 + 1))
    with pytest.raises(InvalidInputError, match="no tiene líneas"):
        await service.upload(manager, "p.xlsx", workbook(HEADER))
    service.max_lines = 2
    with pytest.raises(InvalidInputError, match="máximo de 2 líneas"):
        await service.upload(manager, "p.xlsx", report())


async def test_unknown_batch_and_bad_filters(service, manager) -> None:

    with pytest.raises(NotFoundError):
        await service.get_batch(uuid4())
    with pytest.raises(NotFoundError):
        await service.revalidate(manager, uuid4())
    batch = await service.upload(manager, "p.xlsx", report())
    with pytest.raises(InvalidInputError):
        await service.orders(batch.id, status="liberado")
    with pytest.raises(InvalidInputError):
        await service.orders(batch.id, size=500)


def test_catalogs_label_every_status_and_reason(service) -> None:
    catalogs = service.catalogs()

    assert [item["value"] for item in catalogs["statuses"]] == [
        "listo_para_liberar",
        "pendiente_validacion",
    ]
    ready = [
        item["value"] for item in catalogs["reasons"] if item["status"] == "listo_para_liberar"
    ]
    assert ready == ["cumple_con_apoyo", "cumple_margen_lista"]


async def test_manual_approval_makes_the_order_ready_and_keeps_its_lines(
    service, source, manager
) -> None:
    source.items.append(agreement(supplier_value=D("0.05"), target_margin_pct=D("0.14")))
    batch = await service.upload(manager, "p.xlsx", report())

    approved = await service.approve(manager, batch.id, ["PV-2"], "  Autorizado por gerencia  ")

    assert (approved.ready_order_count, approved.pending_order_count) == (2, 0)
    # Lines keep what the validation said.
    assert (approved.ready_line_count, approved.pending_line_count) == (2, 1)
    page = await service.orders(batch.id, sort="file")
    pv2 = page.items[1]
    assert pv2.status == "listo_para_liberar"
    assert pv2.manual_approval.approved_by == manager.email
    assert pv2.manual_approval.note == "Autorizado por gerencia"
    assert [line.result.reason for line in pv2.lines] == ["cumple_con_apoyo", "sin_acuerdo"]
    assert page.counts == {"listo_para_liberar": 2, "pendiente_validacion": 0}
    assert (await service.orders(batch.id, reason="sin_acuerdo")).total == 1
    assert (await service.orders(batch.id, status="pendiente_validacion")).total == 0


async def test_approval_is_all_or_nothing(service, source, manager) -> None:
    source.items.append(agreement(supplier_value=D("0.05"), target_margin_pct=D("0.14")))
    batch = await service.upload(manager, "p.xlsx", report())

    with pytest.raises(ConflictError, match="PV-1"):
        await service.approve(manager, batch.id, ["PV-2", "PV-1"])
    with pytest.raises(NotFoundError, match="PV-9"):
        await service.approve(manager, batch.id, ["PV-2", "PV-9"])
    assert (await service.get_batch(batch.id)).ready_order_count == 1

    await service.approve(manager, batch.id, ["PV-2"])
    with pytest.raises(ConflictError):
        await service.approve(manager, batch.id, ["PV-2"])
    with pytest.raises(InvalidInputError):
        await service.approve(manager, batch.id, [" "])
    with pytest.raises(InvalidInputError, match="500"):
        await service.approve(manager, batch.id, ["PV-1"], "x" * 501)


async def test_removing_the_approval_and_revalidating(service, source, manager) -> None:
    batch = await service.upload(manager, "p.xlsx", report())
    await service.approve(manager, batch.id, ["PV-1", "PV-2"], None)

    # Re-validating recomputes the lines and keeps the people's decisions.
    again = await service.revalidate(manager, batch.id)
    assert again.ready_order_count == 2 and again.ready_line_count == 0

    await service.remove_approval(manager, batch.id, "PV-2")
    assert (await service.get_batch(batch.id)).ready_order_count == 1
    assert (await service.orders(batch.id, sort="file")).items[1].manual_approval is None
    with pytest.raises(NotFoundError):
        await service.remove_approval(manager, batch.id, "PV-2")


async def test_delete_and_permissions_of_the_new_actions(service, manager) -> None:
    outsider = person("otro@jdh.com.co")
    batch = await service.upload(manager, "p.xlsx", report())

    for action in (
        service.approve(outsider, batch.id, ["PV-1"]),
        service.remove_approval(outsider, batch.id, "PV-1"),
        service.delete(outsider, batch.id),
    ):
        with pytest.raises(ForbiddenError):
            await action

    await service.delete(manager, batch.id)
    with pytest.raises(NotFoundError):
        await service.get_batch(batch.id)
    with pytest.raises(NotFoundError):
        await service.delete(manager, batch.id)


async def test_the_threshold_releases_orders_by_their_margin_and_is_kept(
    service, source, manager
) -> None:
    # PV-1 is one 10 % line against a 10,5 % margin; PV-2 has nothing to compare with.
    source.catalog["18294"] = ProductInfo("18294", {"margin_list": D("0.105")})

    plain = await service.upload(manager, "p.xlsx", report())
    with_threshold = await service.upload(manager, "p.xlsx", report(), D("0.5"))

    assert plain.margin_threshold == D("0") and with_threshold.margin_threshold == D("0.005")
    assert (plain.ready_order_count, with_threshold.ready_order_count) == (0, 1)
    # The lines are the same: the threshold does not act on them.
    assert plain.ready_line_count == with_threshold.ready_line_count == 0
    page = await service.orders(with_threshold.id, sort="file")
    pv1, pv2 = page.items
    assert pv1.status == "listo_para_liberar" and pv1.release_basis == "margen"
    assert pv1.lines[0].result.reason == "sin_acuerdo"
    assert pv1.margin_pct == D("0.100000") and pv1.margin_with_threshold_pct == D("0.105000")
    assert pv2.status == "pendiente_validacion" and pv2.release_basis is None
    assert page.counts == {"listo_para_liberar": 1, "pendiente_validacion": 1}

    # Revalidating keeps the stored threshold; a new value replaces it.
    assert (await service.revalidate(manager, with_threshold.id)).ready_order_count == 1
    cleared = await service.revalidate(manager, with_threshold.id, D("0"))
    assert cleared.ready_order_count == 0 and cleared.margin_threshold == D("0")
    with pytest.raises(InvalidInputError, match="umbral"):
        await service.revalidate(manager, plain.id, D("25"))
    with pytest.raises(InvalidInputError, match="umbral"):
        await service.upload(manager, "p.xlsx", report(), D("-1"))


async def test_the_threshold_can_be_tried_and_then_saved(service, source, manager) -> None:
    source.catalog["18294"] = ProductInfo("18294", {"margin_list": D("0.105")})
    batch = await service.upload(manager, "p.xlsx", report())

    tried = await service.orders(batch.id, margin_threshold_points=D("0.5"))
    saved = await service.get_batch(batch.id)

    assert tried.counts == {"listo_para_liberar": 1, "pendiente_validacion": 1}
    assert saved.margin_threshold == D("0") and saved.ready_order_count == 0
    after = await service.set_threshold(manager, batch.id, D("0.5"))
    assert after.margin_threshold == D("0.005") and after.ready_order_count == 1
    assert (await service.orders(batch.id)).counts == tried.counts
    # A manual approval and the threshold do not cancel each other.
    approved = await service.approve(manager, batch.id, ["PV-2"])
    assert approved.ready_order_count == 2
    assert (await service.set_threshold(manager, batch.id, D("0"))).ready_order_count == 1
    with pytest.raises(ForbiddenError):
        await service.set_threshold(person("otro@jdh.com.co"), batch.id, D("1"))
    with pytest.raises(InvalidInputError, match="umbral"):
        await service.set_threshold(manager, batch.id, D("21"))
    with pytest.raises(NotFoundError):
        await service.set_threshold(manager, uuid4(), D("1"))


async def test_orders_come_with_their_margin_and_the_liquor_tax_flag(
    service, source, manager
) -> None:
    source.catalog["1128"] = ProductInfo("1128", {"margin_list": D("0.18")}, D("6911"))
    batch = await service.upload(manager, "p.xlsx", report())

    page = await service.orders(batch.id, sort="file")
    pv1, pv2 = page.items

    # PV-1 has one 10 % line; PV-2 weights 10 % and 5 % by amount (the same amount: 7,5 %).
    assert pv1.margin_pct == D("0.100000") and not pv1.has_liquor_tax
    assert pv2.margin_pct == D("0.075000") and pv2.has_liquor_tax
    assert pv2.required_margin_pct == D("0.180000")
    assert pv2.without_support_line_count == 2 and pv2.margin_with_threshold_pct is None
    assert [line.result.liquor_tax for line in pv2.lines] == [False, True]


async def test_orders_and_their_lines_come_by_attention_by_default(
    service, source, manager
) -> None:
    source.items.append(agreement(supplier_value=D("0.05"), target_margin_pct=D("0.14")))
    batch = await service.upload(manager, "p.xlsx", report())

    attention = await service.orders(batch.id)
    in_file = await service.orders(batch.id, sort="file")

    # PV-2 has a line with no support, so it goes first, and that line leads inside it.
    assert [order.document for order in attention.items] == ["PV-2", "PV-1"]
    assert [item.line.sku for item in attention.items[0].lines] == ["1128", "28703"]
    assert [order.document for order in in_file.items] == ["PV-1", "PV-2"]
    assert [item.line.sku for item in in_file.items[0 + 1].lines] == ["28703", "1128"]
    with pytest.raises(InvalidInputError, match="sort"):
        await service.orders(batch.id, sort="margen")


async def test_lower_margins_come_first_among_orders_with_support(service, source, manager) -> None:
    def row(document: str, margin: float) -> list:
        values = list(ROW)
        values[2], values[9], values[13] = document, 18294, margin
        values[14] = "359 - LOCERIA COLOMBIANA S.A."
        return values

    source.items.append(agreement(supplier_value=D("0.5"), target_margin_pct=D("0.1")))
    content = workbook(HEADER, row("A", 30.0), row("B", 12.0), row("C", 20.0))
    batch = await service.upload(manager, "p.xlsx", content)

    page = await service.orders(batch.id)

    assert [order.document for order in page.items] == ["B", "C", "A"]


async def test_a_wine_without_its_tax_is_held_back_and_only_a_person_releases_it(
    service, source, manager
) -> None:
    source.catalog["12013"] = ProductInfo("12013", {"margin_list": D("0.57")}, D("6911"))
    header = [*HEADER[:16], "Vlr. imp. IMPTOLIC", HEADER[16]]

    def wine(document: str, tax: int) -> list:
        values = [*ROW[:16], tax, ROW[16]]
        values[2], values[9], values[13] = document, 12013, 62.8
        values[14] = "1002 - MCCAIN"
        return values

    batch = await service.upload(
        manager, "p.xlsx", workbook(header, wine("WITH-TAX", 6911), wine("NO-TAX", 0)), D("20")
    )

    page = await service.orders(batch.id, sort="file")
    with_tax, without_tax = page.items
    # Both meet the margin; the one without the tax stays pending, whatever the threshold.
    assert with_tax.status == "listo_para_liberar" and with_tax.release_basis == "lineas"
    assert without_tax.status == "pendiente_validacion" and without_tax.release_basis is None
    assert without_tax.lines[0].result.reason == "falta_impuesto_licor"
    assert without_tax.has_liquor_tax and not with_tax.has_liquor_tax
    assert (batch.ready_order_count, batch.pending_order_count) == (1, 1)
    # A person can release it.
    approved = await service.approve(manager, batch.id, ["NO-TAX"])
    assert approved.ready_order_count == 2


async def test_orders_released_by_margin_say_whether_the_threshold_was_needed(
    service, source, manager
) -> None:
    # PV-1: a 10 % line against 10,5 % (needs the threshold); PV-2 has nothing to compare.
    source.catalog["18294"] = ProductInfo("18294", {"margin_list": D("0.105")})
    batch = await service.upload(manager, "p.xlsx", report(), D("0.5"))

    page = await service.orders(batch.id, sort="file")
    by_threshold, other = page.items

    assert by_threshold.release_basis == "margen" and by_threshold.released_by_threshold
    assert other.release_basis is None and not other.released_by_threshold
    # Raise the target below the margin: released by margin alone, no threshold needed.
    source.catalog["18294"] = ProductInfo("18294", {"margin_list": D("0.09")})
    again = await service.revalidate(manager, batch.id)
    page = await service.orders(again.id, sort="file")
    # The line now meets the margin, so the order is released by its lines.
    assert page.items[0].release_basis == "lineas" and not page.items[0].released_by_threshold


async def test_the_page_tells_whether_the_threshold_releases_any_order(
    service, source, manager
) -> None:
    source.catalog["18294"] = ProductInfo("18294", {"margin_list": D("0.105")})
    batch = await service.upload(manager, "p.xlsx", report())

    none = await service.orders(batch.id)
    tried = await service.orders(batch.id, margin_threshold_points=D("0.5"))
    searched = await service.orders(batch.id, margin_threshold_points=D("0.5"), q="no-such-order")

    assert none.threshold_released_count == 0
    assert tried.threshold_released_count == 1
    # A search does not change whether the threshold releases something in the file.
    assert searched.total == 0 and searched.threshold_released_count == 1
    await service.set_threshold(manager, batch.id, D("0.5"))
    assert (await service.orders(batch.id)).threshold_released_count == 1
    # The orders it releases are the same that carry the per-order mark.
    page = await service.orders(batch.id, sort="file")
    assert [order.document for order in page.items if order.released_by_threshold] == ["PV-1"]
