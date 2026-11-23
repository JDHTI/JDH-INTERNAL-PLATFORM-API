"""The bulk template's header colors and what a load takes from the customer and product masters
(also with Excel numbers, check digits and leading zeros)."""

from __future__ import annotations

import io
from dataclasses import replace
from decimal import Decimal

import openpyxl
import pytest

from app.modules.commercial_masters.domain.entities import nit_check_digit
from app.modules.supplier_supports.infrastructure.bulk_file import template_workbook

RED, ORANGE, BLUE = "B3261E", "B45309", "1F3A5F"


def colors(content: bytes) -> dict[str, str]:
    sheet = openpyxl.load_workbook(io.BytesIO(content))["Apoyos"]
    return {cell.value: cell.fill.start_color.rgb[-6:] for cell in sheet[1]}


def comments(content: bytes) -> dict[str, str]:
    sheet = openpyxl.load_workbook(io.BytesIO(content))["Apoyos"]
    return {cell.value: cell.comment.text for cell in sheet[1] if cell.comment}


def xlsx(header: list[str], *rows: list[object]) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Apoyos"
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


async def load(bulk, manager, content: bytes, **kwargs):
    return await bulk.load(manager, "apoyos.xlsx", content, supplier_code="233", **kwargs)


HEADER = [
    "Clase apoyo",
    "Tipo apoyo",
    "Valor apoyo",
    "Unidad valor",
    "NIT Cliente",
    "Sucursal",
    "SKU JDH",
]


def test_headers_are_red_when_always_required_orange_by_type_and_blue_otherwise() -> None:
    short = colors(template_workbook())

    assert short["Clase apoyo"] == RED and short["Tipo apoyo"] == RED
    # Creating a support needs its value unit, in every type but the margin exception
    # (2026-11-20): red, with the exception in the header's note.
    assert short["Unidad valor"] == RED
    assert comments(template_workbook())["Unidad valor"] == (
        "Obligatorio: Sí, salvo con Tipo apoyo Margen mínimo autorizado."
    )
    # Every type but the minimum margin needs a value; the margin column is the minimum
    # margin's and the guaranteed margin's.
    assert short["Valor apoyo"] == ORANGE
    assert short["Excepción margen mínimo autorizado JDH"] == ORANGE
    assert {short[name] for name in ("Fecha Inicio", "NIT Cliente", "SKU JDH", "Bodega")} == {BLUE}
    grouped = colors(template_workbook(groups=True))
    assert grouped["Grupo Empresarial"] == RED
    exported = colors(template_workbook(validation=True))
    assert exported["Valor apoyo"] == ORANGE
    assert exported["Validación"] == BLUE and exported["Observaciones"] == BLUE
    # The full template has a column per margin: the short one's stops being required.
    full = colors(template_workbook(full=True))
    # The exception's margin is «Excepción margen mínimo autorizado JDH» in every template.
    assert full["Margen garantizado"] == ORANGE and full["Margen Mínimo"] == BLUE
    assert full["Excepción margen mínimo autorizado JDH"] == ORANGE


async def test_a_type_fixed_by_the_profile_makes_its_fields_always_required(
    profiles, manager
) -> None:
    profile = await profiles.save(
        manager,
        "233",
        defaults={"calc_method": "pct_costo"},
        column_map={},
        sheet=None,
        notes=None,
        expected_version=None,
    )

    own = colors(template_workbook(profile))

    assert "Tipo apoyo" not in own and own["Valor apoyo"] == RED
    assert own["Excepción margen mínimo autorizado JDH"] == BLUE


def test_the_instructions_explain_the_colors_and_which_types_require_a_column() -> None:
    guide = openpyxl.load_workbook(io.BytesIO(template_workbook()))["Instrucciones"]
    rows = {row[0]: row for row in guide.iter_rows(values_only=True) if row[0]}
    swatches = {row[0].value: row[0].fill.start_color.rgb[-6:] for row in guide.iter_rows()}

    assert rows["Rojo"][1] == (
        "Obligatoria en toda fila (Unidad valor: en la que crea un apoyo, salvo la "
        "Excepción margen JDH)."
    )
    assert (swatches["Rojo"], swatches["Naranja"], swatches["Azul"]) == (RED, ORANGE, BLUE)
    assert rows["value_unit"][2] == "Sí, salvo con Tipo apoyo Margen mínimo autorizado"
    unit = next(line for line in rows if str(line).startswith("Unidad valor:"))
    assert "obligatoria en la fila que crea un apoyo" in unit and "conserva la guardada" in unit
    assert rows["supplier_value"][2] == (
        "Sí, salvo con Tipo apoyo % Margen Garantizado o Margen mínimo autorizado"
    )
    assert rows["supported_margin_pct"][2] == (
        "Con Tipo apoyo % Margen Garantizado o Margen mínimo autorizado"
    )
    assert rows["kind"][2] == "Sí" and not rows["product_sku"][2]


async def test_a_blank_class_is_an_error_when_the_file_has_the_column(bulk, manager) -> None:
    content = xlsx(HEADER, [None, "% Apoyo en Costo", 0.05, "Unidad", 800116299, None, 1128])

    report = await load(bulk, manager, content)

    assert report.blocked
    assert (report.errors[0].field, report.errors[0].message[:22]) == (
        "kind",
        "Falta la Clase apoyo: ",
    )


async def test_the_margin_column_is_the_guaranteed_margin_of_a_guaranteed_margin(
    bulk, repo, manager
) -> None:
    header = [
        *HEADER,
        "Excepción margen mínimo autorizado JDH",
        "Margen mínimo garantizado proveedor",
    ]
    row = ["Apoyo táctico", "% Margen Garantizado", 5000, "Unidad", 800116299, None, 1128, 0.2]

    report = await load(bulk, manager, xlsx(header, [*row, 0.13]), apply=True)

    assert report.errors == []
    (saved,) = repo.items.values()
    assert saved.data.target_margin_pct == Decimal("0.2")
    assert saved.data.supplier_min_margin_pct == Decimal("0.13")
    assert saved.data.supported_margin_pct is None
    # A new % Margen Garantizado needs the supplier's minimum margin (2026-11-23); a negative
    # one is the supplier's exception and is read.
    missing = await load(bulk, manager, xlsx(header, [*row[:6], 8749, 0.2, None]))
    assert missing.blocked and missing.errors[0].field == "supplier_min_margin_pct"
    negative = await load(bulk, manager, xlsx(header, [*row[:6], 8749, 0.2, "-5%"]))
    assert negative.errors == []


def test_the_dian_check_digit() -> None:
    assert nit_check_digit("800197268") == "4"  # the DIAN's own NIT
    assert nit_check_digit("800116299") == "6"


@pytest.mark.parametrize(
    ("nit", "sku", "branch"),
    [
        (800116299, 1128, 2),  # Excel numbers
        (800116299.0, 1128.0, 2.0),  # Excel floats
        ("800116299-6", "01128", "002"),  # check digit, leading zero, the master's branch
        ("8001162996", " 1128 ", "2"),  # check digit glued to the NIT
    ],
)
async def test_customer_and_product_come_from_the_masters_however_they_are_typed(
    bulk, repo, lookup, manager, nit, sku, branch
) -> None:
    lookup.products["1128"] = replace(lookup.products["1128"], supplier_sku="FAM-77")
    header = [*HEADER, "Cliente", "Grupo Empresarial", "SKU Proveedor"]
    row = [
        "Apoyo táctico",
        "% Apoyo en Costo",
        0.05,
        "Unidad",
        nit,
        branch,
        sku,
        "OTRO",
        "OTRO",
        "X",
    ]

    report = await load(bulk, manager, xlsx(header, row), apply=True)

    assert report.errors == []
    (saved,) = repo.items.values()
    data = saved.data
    assert (data.customer_nit, data.customer_branch_id) == ("800116299", "002")
    assert data.customer_name == "LIMPIEZA Y MANTENIMIENTO DE ANTIOQUIA SAS"
    assert (data.product_sku, data.supplier_sku, data.brand) == ("1128", "FAM-77", "FAMILIA")


async def test_a_branch_the_customer_does_not_have_is_an_error(bulk, manager) -> None:
    row = ["Apoyo táctico", "% Apoyo en Costo", 0.05, "Unidad", 800116299, 9, 1128]

    report = await load(bulk, manager, xlsx(HEADER, row))

    assert report.blocked and report.errors[0].field == "customer_branch_id"
    assert "La sucursal 9 no existe para el NIT 800116299" in report.errors[0].message


async def test_the_business_group_of_a_nit_is_the_masters(bulk, repo, manager) -> None:
    header = [*HEADER, "Grupo Empresarial"]
    row = ["Apoyo táctico", "% Apoyo en Costo", 0.05, "Unidad", 900430148, None, 1128, "Otro grupo"]

    report = await load(bulk, manager, xlsx(header, row), apply=True)

    assert report.errors == []
    (saved,) = repo.items.values()
    assert saved.data.customer_group == "Grupo Goddard"


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (72163, None),  # Familia's code of JDH 1128, typed as a number
        ("00000", "no está en el maestro de productos"),
        ("99999", "corresponde a varias referencias JDH"),
    ],
)
async def test_a_supplier_sku_without_jdh_sku_finds_it_or_fails(
    bulk, repo, manager, code, message
) -> None:
    header = ["Clase apoyo", "Tipo apoyo", "Valor apoyo", "Unidad valor", "SKU Proveedor"]
    row = ["Actividad descuento temporal", "% Apoyo en Costo", 0.05, "Unidad", code]

    report = await load(bulk, manager, xlsx(header, row), apply=message is None)

    if message is None:
        assert report.errors == []
        assert next(iter(repo.items.values())).data.product_sku == "1128"
    else:
        assert report.blocked and report.errors[0].field == "supplier_sku"
        assert message in report.errors[0].message


async def test_promotional_warehouses_come_from_the_load_or_from_each_row(
    bulk, repo, manager
) -> None:
    header = [*HEADER, "Bodega promocional"]
    rows = [
        # the default
        ["Apoyo táctico", "% Apoyo en Costo", 0.05, "Unidad", 800116299, None, 1128, None],
        # its own
        ["Apoyo táctico", "% Apoyo en Costo", 0.05, "Unidad", 900430148, None, 1128, "No"],
    ]

    report = await load(
        bulk, manager, xlsx(header, *rows), apply=True, defaults={"promo_warehouses": "true"}
    )

    assert report.errors == []
    by_nit = {item.data.customer_nit: item.data.promo_warehouses for item in repo.items.values()}
    assert by_nit == {"800116299": True, "900430148": False}
    bad = await load(bulk, manager, xlsx(header, [*rows[0][:-1], "Tal vez"]))
    assert bad.blocked and bad.errors[0].field == "promo_warehouses"
    assert "escribe Sí o No" in bad.errors[0].message


async def test_a_profile_can_fix_the_promotional_warehouses(profiles, manager) -> None:
    profile = await profiles.save(
        manager,
        "233",
        defaults={"promo_warehouses": "Sí"},
        column_map={},
        sheet=None,
        notes=None,
        expected_version=None,
    )

    assert profile.data.defaults["promo_warehouses"] is True


def test_the_promotional_column_is_in_the_full_template_and_the_download_only() -> None:
    assert "Bodega promocional" not in colors(template_workbook())
    assert colors(template_workbook(full=True))["Bodega promocional"] == BLUE
    exported = openpyxl.load_workbook(
        io.BytesIO(template_workbook(rows=[{"promo_warehouses": True}], validation=True))
    )["Apoyos"]
    headers = [cell.value for cell in exported[1]]
    column = headers.index("Bodega promocional") + 1
    assert headers[column - 2] == "Bodega" and exported.cell(row=2, column=column).value == "Sí"


async def test_the_download_with_data_reads_the_masters_again(bulk, repo, lookup, manager) -> None:
    row = ["Apoyo táctico", "% Apoyo en Costo", 0.05, "Unidad", 900430148, None, 1128]
    await load(bulk, manager, xlsx(HEADER, row), apply=True)
    lookup.products["1128"] = replace(lookup.products["1128"], supplier_sku="FAM-NUEVO")
    lookup.names_by_nit["900430148"] = "GODDARD RENOMBRADO"

    (exported,) = await bulk.export_rows(manager, "233")

    assert exported["supplier_sku"] == "FAM-NUEVO"
    assert exported["customer_name"] == "GODDARD RENOMBRADO"
    assert exported["customer_group"] == "Grupo Goddard"
