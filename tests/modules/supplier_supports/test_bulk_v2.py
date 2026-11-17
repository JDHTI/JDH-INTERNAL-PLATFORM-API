"""Rules v2 in the bulk load: crossings, the «Validación» column and the business-group upload."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import InvalidInputError

HEADER = "Clase apoyo,Tipo apoyo,Valor apoyo,Fecha Inicio,Fecha Fin,NIT,SKU JDH\n"
ROW = "Apoyo táctico,% Apoyo en Costo,5%,01/07/2026,31/12/2026,800116299,1128\n"


async def load(bulk, manager, content: str, **kwargs):
    return await bulk.load(
        manager, "apoyos.csv", content.encode("utf-8"), supplier_code="233", **kwargs
    )


async def test_kind_and_type_labels_are_read(bulk, repo, manager) -> None:
    report = await load(bulk, manager, HEADER + ROW, apply=True)

    assert report.errors == [] and report.to_create == 1
    saved = next(iter(repo.items.values()))
    assert (saved.data.kind, saved.data.calc_method) == ("tactico_cliente", "pct_costo")


async def test_a_row_that_crosses_a_stored_support_blocks_or_expires_it(
    bulk, repo, manager
) -> None:
    await load(bulk, manager, HEADER + ROW, apply=True)
    first = next(iter(repo.items.values()))
    crossing = HEADER + ROW.replace("01/07/2026", "01/10/2026").replace("31/12/2026", "")

    blocked = await load(bulk, manager, crossing, apply=True)
    assert blocked.blocked and "Se cruza con un apoyo vigente" in blocked.errors[0].message

    report = await load(bulk, manager, crossing, apply=True, on_overlap="vencer_anterior")
    assert report.applied and report.to_retire == 1 and report.retired == [first.id]
    assert repo.items[first.id].data.valid_to == date(2026, 9, 30)


async def test_two_rows_of_the_file_that_cross_are_an_error(bulk, manager) -> None:
    other = ROW.replace("01/07/2026", "01/12/2026").replace("31/12/2026", "")

    report = await load(bulk, manager, HEADER + ROW + other)

    assert report.blocked and "Se cruza con la fila 2" in report.errors[0].message


async def test_validation_no_expires_blank_skips_and_si_updates(bulk, repo, manager) -> None:
    rows = ROW + ROW.replace("1128", "8749") + ROW.replace("1128", "3474")
    await load(bulk, manager, HEADER + rows, apply=True)
    by_sku = {item.data.product_sku: item.id for item in repo.items.values()}
    header = HEADER.replace("\n", ",Validación,Observaciones\n")
    validated = (
        ROW.replace("\n", ",No,El proveedor ya no lo apoya\n")
        + ROW.replace("1128", "8749").replace("5%", "6%").replace("\n", ",Sí,\n")
        + ROW.replace("1128", "3474").replace("\n", ",,\n")
    )

    report = await load(bulk, manager, header + validated, apply=True)

    assert report.errors == [] and report.skipped == 1
    assert (report.to_retire, report.to_update) == (1, 1)
    expired = repo.items[by_sku["1128"]].data
    assert expired.valid_to == date(2026, 10, 8)  # yesterday
    assert repo.items[by_sku["8749"]].data.supplier_value == Decimal("0.06")
    bad = await load(bulk, manager, header + ROW.replace("\n", ",Tal vez,\n"))
    assert bad.blocked and "escribe Sí o No" in bad.errors[0].message


async def test_a_business_group_row_becomes_one_support_per_customer(bulk, repo, manager) -> None:
    header = "Clase apoyo,Tipo apoyo,Valor apoyo,Fecha Inicio,Fecha Fin,Grupo Empresarial,SKU JDH\n"
    row = "Apoyo táctico,% Apoyo en Costo,5%,01/07/2026,31/12/2026,Grupo Goddard,1128\n"

    report = await load(bulk, manager, header + row, apply=True, groups=True)

    assert report.errors == [] and report.group_supports == 2 and report.to_create == 2
    customers = {item.data.customer_nit for item in repo.items.values()}
    assert customers == {"900430148", "830012863"}
    assert {item.data.customer_group for item in repo.items.values()} == {"Grupo Goddard"}
    unknown = await load(bulk, manager, header + row.replace("Goddard", "Nadie"), groups=True)
    assert unknown.blocked and "no tiene clientes activos" in unknown.errors[0].message
    with pytest.raises(InvalidInputError, match="columna Grupo"):
        await load(bulk, manager, HEADER + ROW, groups=True)
