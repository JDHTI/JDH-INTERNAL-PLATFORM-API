"""Use cases of the retained orders validator over in-memory ports."""

from decimal import Decimal

import pytest
from app.core.errors import ForbiddenError, InvalidInputError, NotFoundError

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

    page = await service.orders(batch.id)
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
    source.margins["233"] = D("0.05")
    again = await service.revalidate(manager, batch.id)

    assert (again.ready_line_count, again.ready_order_count) == (3, 2)
    reasons = [
        line.result.reason
        for order in (await service.orders(batch.id)).items
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
    from uuid import uuid4

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
