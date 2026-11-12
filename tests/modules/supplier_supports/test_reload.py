"""Reload of supplier files by key, over in-memory ports."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from app.modules.supplier_supports.application.reload import (
    ReloadRow,
    ReloadTable,
    SupportReloadService,
    provided_fields,
)
from app.modules.supplier_supports.domain.entities import SupportData, Trace

LABEL = "Familia/Apoyos_Familia"
MAPPED = ("customer_nit", "product_sku", "supplier_value", "reference_cost", "valid_from")


def row(**changes) -> ReloadRow:
    data = SupportData(
        kind="tactico_cliente",
        calc_method="costo_neto",
        customer_nit="800116299",
        product_sku="1128",
        supplier_value=Decimal("66823.566"),
        reference_cost=Decimal("87275"),
        value_unit="caja",
        valid_from=date(2026, 2, 1),
        source=LABEL,
    )
    data = replace(data, **changes)
    return ReloadRow(data, "233", provided_fields(data, MAPPED))


async def stored(service, repo, **changes):
    """A support as the first import left it: bound to the masters."""

    base = SupportData(
        kind="tactico_cliente",
        calc_method="costo_neto",
        customer_nit="800116299",
        product_sku="1128",
        supplier_value=Decimal("66823.566"),
        reference_cost=Decimal("80000"),
        value_unit="caja",
        valid_from=date(2026, 2, 1),
        source=LABEL,
        jdh_pct=Decimal("0.02"),
    )
    data = await service._resolve(
        service._normalize(replace(base, **changes)), supplier_code="233", strict=False
    )
    return await repo.create(uuid4(), data, "importacion", datetime.now(UTC))


async def test_known_keys_update_only_what_changed_and_never_the_file_cost(
    service, repo, tmp_path
) -> None:
    same = await stored(service, repo)
    changed = await stored(service, repo, product_sku="8749")
    gone = await stored(service, repo, product_sku="3474", valid_to=date(2026, 12, 31))
    other_table = await stored(service, repo, product_sku="1001", source="Familia/Tabla40")
    file = tmp_path / "Apoyos Proveedor - Familia.xlsx"
    file.write_bytes(b"x")
    reload = SupportReloadService(service)

    report = await reload.plan(
        file,
        [
            ReloadTable(
                LABEL,
                (
                    # Only the file's cost differs: it is a lookup, the DWH gives the cost.
                    row(reference_cost=Decimal("99999.123456")),
                    row(
                        product_sku="8749", supplier_value=Decimal("70000"), jdh_pct=Decimal("0.05")
                    ),
                    row(product_sku="999999"),  # unknown SKU: created for review, not blocked
                ),
            )
        ],
        expire_missing=True,
    )

    table = report.tables[0]
    assert (table.create, table.update, table.unchanged) == (1, 1, 1)
    assert table.missing == 1 and table.expire == 1  # 3474; Tabla40 is another table
    assert table.changed_fields == {"supplier_value": 1}
    new_data = report.plan.creates[0][1]
    assert new_data.status == "pendiente_revision" and new_data.issues[0].code == "sku_sin_cruce"

    await reload.apply(report)

    assert repo.items[same.id].version == 1
    assert repo.items[changed.id].data.supplier_value == Decimal("70000")
    assert repo.items[changed.id].data.jdh_pct == Decimal("0.02")  # the panel's, kept
    assert repo.items[gone.id].data.valid_to == date(2026, 10, 8)
    assert repo.items[other_table.id].version == 1
    assert repo.traces[changed.id] == Trace("carga", report.plan.load_id)
    assert repo.loads[0].file_name == "Apoyos Proveedor - Familia.xlsx"
