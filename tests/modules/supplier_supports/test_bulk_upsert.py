"""Bulk load by key: create, update, unchanged, missing and expired, with the trace."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.modules.supplier_supports.application.bulk import QuietHours
from app.modules.supplier_supports.domain.entities import Trace

# The v0.3 TI template headers (supplier chosen in the Suite) plus the optional JDH column.
V03 = "Vigencia inicial,Vigencia final,NIT,SKU JDH,Tipo apoyo,Valor apoyo proveedor\n"
ROWS = (
    "01/10/2026,31/12/2026,800116299,1128,% sobre costo,5%\n"
    "01/10/2026,INDEFINIDO,,8749,% sobre precio lista,7\n"
)


async def load(bulk, manager, content: str, **kwargs):
    return await bulk.load(manager, "apoyos.csv", content.encode("utf-8"), **kwargs)


async def test_the_v03_template_reads_labels_and_derives_the_class(bulk, repo, manager) -> None:
    report = await load(bulk, manager, V03 + ROWS, supplier_code="233", apply=True)

    assert report.errors == [] and report.applied and report.to_create == 2
    saved = {item.data.product_sku: item for item in repo.items.values()}
    assert saved["1128"].data.calc_method == "pct_costo"
    assert saved["1128"].data.kind == "tactico_cliente"  # it has a client
    assert saved["8749"].data.calc_method == "pct_precio_lista"
    assert saved["8749"].data.kind == "actividad_temporal"  # for every client
    assert saved["8749"].data.valid_to is None
    assert {repo.traces[item.id] for item in repo.items.values()} == {
        Trace("carga", report.load_id)
    }
    assert repo.loads[0].file_sha256 and repo.loads[0].supplier_code == "233"


async def test_a_second_upload_updates_by_key_and_keeps_what_it_does_not_bring(
    bulk, repo, manager
) -> None:
    first = await load(bulk, manager, V03 + ROWS, supplier_code="233", apply=True)
    target = next(item for item in repo.items.values() if item.data.product_sku == "1128")
    # The JDH contribution was approved in the panel meanwhile.
    repo.items[target.id] = replace(
        target, data=replace(target.data, jdh_pct=Decimal("0.02")), version=target.version + 1
    )

    changed = ROWS.replace("% sobre costo,5%", "% sobre costo,6%")
    report = await load(bulk, manager, V03 + changed, supplier_code="233", apply=True)

    assert (report.to_create, report.to_update, report.unchanged) == (0, 1, 1)
    assert [row.action for row in report.preview] == ["actualizar", "sin_cambio"]
    updated = repo.items[target.id]
    assert updated.data.supplier_value == Decimal("0.06")
    # The file has no JDH column: the approved contribution stays.
    assert updated.data.jdh_pct == Decimal("0.02")
    assert repo.traces[target.id] == Trace("carga", report.load_id)
    assert report.load_id != first.load_id and len(repo.items) == 2

    # Even a file that brings the JDH column does not replace an approved contribution.
    with_jdh = V03.replace("\n", ",% Apoyo JDH\n", 1) + changed.replace("6%\n", "6%,5%\n", 1)
    again = await load(bulk, manager, with_jdh, supplier_code="233", apply=True)
    assert again.to_update == 0 and repo.items[target.id].data.jdh_pct == Decimal("0.02")


async def test_missing_supports_are_reported_and_only_expired_when_asked(
    bulk, repo, manager
) -> None:
    client_rows = ROWS.replace(",,8749", ",800116299,8749")  # both for the same client
    await load(bulk, manager, V03 + client_rows, supplier_code="233", apply=True)
    only_one = V03 + client_rows.splitlines()[0] + "\n"
    gone = next(item for item in repo.items.values() if item.data.product_sku == "8749")

    report = await load(bulk, manager, only_one, supplier_code="233")
    assert report.missing == 1 and report.to_expire == 0
    assert report.missing_preview[0].support_id == gone.id
    assert not report.missing_preview[0].expires

    # Today is 2026-10-09 (FixedCalendar) and it started 2026-10-01: closed yesterday.
    applied = await load(
        bulk, manager, only_one, supplier_code="233", apply=True, expire_missing=True
    )
    assert applied.to_expire == 1 and applied.expired == [gone.id]
    assert repo.items[gone.id].data.valid_to == date(2026, 10, 8)
    assert repo.traces[gone.id] == Trace("vencimiento", applied.load_id)


async def test_missing_only_counts_the_classes_the_file_brings(bulk, repo, manager) -> None:
    await load(bulk, manager, V03 + ROWS, supplier_code="233", apply=True)

    # Only the client row (tactico_cliente): the activity for everyone is another class.
    report = await load(bulk, manager, V03 + ROWS.splitlines()[0] + "\n", supplier_code="233")

    assert report.missing == 0 and report.unchanged == 1


async def test_a_renewal_with_a_new_start_is_a_new_support(bulk, repo, manager) -> None:
    await load(bulk, manager, V03 + ROWS, supplier_code="233", apply=True)
    renewed = ROWS.replace("01/10/2026,31/12/2026", "01/01/2027,31/12/2027")

    report = await load(bulk, manager, V03 + renewed, supplier_code="233")

    assert (report.to_create, report.unchanged, report.missing) == (1, 1, 1)


async def test_rows_with_the_same_key_block_the_load(bulk, manager) -> None:
    line = ROWS.splitlines()[0] + "\n"
    report = await load(bulk, manager, V03 + line + line.replace("5%", "8%"), supplier_code="233")

    assert report.blocked and report.errors[0].row_number == 3
    assert "misma llave" in report.errors[0].message


async def test_an_unknown_type_lists_the_labels(bulk, manager) -> None:
    report = await load(
        bulk, manager, V03 + "01/10/2026,,800116299,1128,regalo,5%\n", supplier_code="233"
    )

    assert report.errors[0].field == "calc_method"
    assert "% Apoyo en Costo" in report.errors[0].message


async def test_the_supported_margin_column_and_the_minimum_margin_type(bulk, repo, manager) -> None:
    header = "Fecha Inicio,NIT Cliente,SKU JDH,Tipo de apoyo,Valor apoyo,Margen apoyado\n"
    rows = (
        "01/10/2026,800116299,1128,% sobre costo,10%,12%\n"
        "01/10/2026,800116299,8749,Margen mínimo,,6%\n"
    )

    report = await load(bulk, manager, header + rows, supplier_code="233", apply=True)

    assert report.errors == [] and report.to_create == 2
    saved = {item.data.product_sku: item.data for item in repo.items.values()}
    assert saved["1128"].supported_margin_pct == Decimal("0.12")
    assert saved["8749"].kind == "excepcion_margen"
    assert saved["8749"].min_margin_pct == Decimal("0.06")


def test_big_loads_wait_for_the_dwh_quiet_hours() -> None:
    zone = ZoneInfo("America/Bogota")
    hours = QuietHours(start=time(18), end=time(3), zone=zone, defer_over=5000)
    noon = datetime(2026, 11, 10, 12, tzinfo=zone).astimezone(UTC)
    night = datetime(2026, 11, 10, 22, tzinfo=zone).astimezone(UTC)
    dawn = datetime(2026, 11, 11, 2, tzinfo=zone).astimezone(UTC)

    assert hours.not_before(100, noon) is None
    assert hours.not_before(6000, noon) == datetime(2026, 11, 10, 18, tzinfo=zone)
    assert hours.not_before(6000, night) is None and hours.not_before(6000, dawn) is None
