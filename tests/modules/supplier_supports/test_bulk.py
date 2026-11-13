"""Bulk load of supports and supplier profiles, over in-memory ports."""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

import openpyxl
import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError
from app.modules.supplier_supports.infrastructure.bulk_file import template_headers
from tests.modules.supplier_supports.conftest import person

GENERAL = (
    "Cod. Proveedor,Tipo,Método de cálculo,Valor Proveedor,Referencia JDH,NIT Cliente,"
    "Fecha Inicio,Fecha Fin,Programa\n"
    "233,tactico_cliente,pct_costo,5%,1128,800116299,01/10/2026,31/12/2026,PLAN 10X10\n"
    "233,tactico_cliente,pct_costo,0.07,8749,,2026-10-01,,PLAN 7\n"
)


async def load(bulk, manager, content: str | bytes, name: str = "apoyos.csv", **kwargs):
    data = content.encode("utf-8") if isinstance(content, str) else content
    return await bulk.load(manager, name, data, **kwargs)


async def test_general_template_file_is_simulated_without_writing(bulk, repo, manager) -> None:
    report = await load(bulk, manager, GENERAL)

    assert (report.blocked, report.applied, report.rows_read) == (False, False, 2)
    assert report.valid == 2 and report.invalid == 0 and report.errors == []
    assert report.preview[0].supplier_value == Decimal("0.05")
    assert report.preview[1].valid_from == date(2026, 10, 1)
    assert report.mapped_columns["product_sku"] == ["Referencia JDH"]
    assert repo.items == {}


async def test_apply_creates_every_row_with_its_origin(bulk, repo, manager) -> None:
    report = await load(bulk, manager, GENERAL, apply=True)

    assert report.applied and len(report.created) == 2
    saved = sorted(repo.items.values(), key=lambda item: item.data.product_sku or "")
    assert [item.data.product_sku for item in saved] == ["1128", "8749"]
    assert saved[0].data.customer_name == "LIMPIEZA Y MANTENIMIENTO DE ANTIOQUIA SAS"
    assert saved[0].data.source == "carga masiva: apoyos.csv"
    assert saved[0].created_by == manager.email


async def test_one_bad_row_blocks_the_whole_load_even_when_applying(bulk, repo, manager) -> None:
    content = GENERAL + "233,tactico_cliente,pct_costo,5%,999999,,01/10/2026,,X\n"

    report = await load(bulk, manager, content, apply=True)

    assert report.blocked and not report.applied and report.created == []
    assert report.valid == 2 and report.invalid == 1
    assert [(issue.row_number, issue.field, issue.message) for issue in report.errors] == [
        (4, "product_sku", "Referencia JDH desconocida: 999999")
    ]
    assert repo.items == {}


async def test_unknown_masters_and_rule_errors_name_their_field(bulk, manager) -> None:
    header = (
        "Tipo,Método de cálculo,Valor Proveedor,Referencia JDH,NIT Cliente,Fecha Inicio,Fecha Fin\n"
    )
    rows = (
        "tactico_cliente,pct_costo,5%,1128,123,01/10/2026,\n"  # unknown client
        "tactico_cliente,pct_costo,5%,1128,,01/10/2026,01/09/2026\n"  # ends before it starts
        "tactico_cliente,precio_neto,5000,1128,,01/10/2026,\n"  # net price needs the margin
    )

    report = await load(bulk, manager, header + rows, supplier_code="233")

    assert [(issue.row_number, issue.field) for issue in report.errors] == [
        (2, "customer_nit"),
        (3, "valid_to"),
        (4, "target_margin_pct"),
    ]


async def test_every_bad_row_is_reported_in_one_pass(bulk, manager) -> None:
    content = (
        "Tipo,Método de cálculo,Valor Proveedor,Referencia JDH,Fecha Inicio\n"
        "tactico_cliente,pct_costo,abc,1128,01/10/2026\n"
        "inventado,pct_costo,5%,1128,01/10/2026\n"
        "tactico_cliente,pct_costo,5%,1128,31/02/xx\n"
    )

    report = await load(bulk, manager, content, supplier_code="233")

    by_row = {issue.row_number: issue for issue in report.errors}
    assert set(by_row) == {2, 3, 4}
    assert "no es un porcentaje" in by_row[2].message and by_row[2].field == "supplier_value"
    assert "valor no válido" in by_row[3].message and by_row[3].field == "kind"
    assert "fecha no válida" in by_row[4].message and by_row[4].field == "valid_from"


async def test_rows_repeated_in_the_file_are_flagged(bulk, manager) -> None:
    line = "233,tactico_cliente,pct_costo,5%,1128,800116299,01/10/2026,31/12/2026,PLAN\n"
    header = GENERAL.splitlines()[0] + "\n"

    report = await load(bulk, manager, header + line + line)

    assert [(issue.row_number, issue.message) for issue in report.errors] == [
        (
            3,
            "Fila repetida: misma llave (proveedor, NIT, SKU, acuerdo y fecha inicio) "
            "que la fila 2",
        )
    ]


async def test_supplier_is_required_from_the_request_or_a_column(bulk, manager) -> None:
    content = (
        "Tipo,Método de cálculo,Valor Proveedor,Referencia JDH\ntactico_cliente,pct_costo,5%,1128\n"
    )

    report = await load(bulk, manager, content)

    assert report.errors[0].field == "supplier_code"
    ok = await load(bulk, manager, content, supplier_code="233")
    assert ok.errors == []


async def test_a_suppliers_own_layout_is_read_through_its_profile(
    bulk, profiles, repo, manager
) -> None:
    await profiles.save(
        manager,
        "233",
        defaults={
            "kind": "Táctico Cliente",
            "calc_method": "pct_costo",
            "regionals": ["antioquia"],
        },
        column_map={
            "product_sku": ["REF FAMILIA JDH"],
            "supplier_value": ["% APOYO"],
            "valid_from": ["VIGENCIA INICIAL"],
            "valid_to": ["VIGENCIA FINAL"],
        },
        sheet=None,
        notes=None,
        expected_version=None,
    )
    content = (
        "Familia Sancela,,,\n"
        "REF FAMILIA JDH,% APOYO,VIGENCIA INICIAL,VIGENCIA FINAL,Columna ajena\n"
        "1128,4.5,01/10/2026,31/12/2026,x\n"
    )

    report = await load(bulk, manager, content, supplier_code="233", apply=True)

    assert report.errors == []
    assert report.profile_used and report.header_row == 2 and report.applied
    assert report.ignored_columns == ["Columna ajena"]
    saved = next(iter(repo.items.values())).data
    assert saved.supplier_value == Decimal("0.045") and saved.regionals == ("ANTIOQUIA",)
    assert (saved.kind, saved.calc_method) == ("tactico_cliente", "pct_costo")


async def test_mapping_and_defaults_can_be_sent_with_the_request(bulk, manager) -> None:
    content = "SKU,DESCUENTO\n1128,0.1\n"

    unmapped = await load(bulk, manager, content, supplier_code="233")
    assert unmapped.errors  # no kind or calc method anywhere
    sent = await load(
        bulk,
        manager,
        content,
        supplier_code="233",
        defaults={"kind": "actividad_temporal", "calc_method": "pct_costo"},
        column_map={"supplier_value": ["DESCUENTO"]},
    )
    assert sent.errors == [] and sent.preview[0].supplier_value == Decimal("0.1")


async def test_excel_file_with_real_dates_and_numbers(bulk, repo, manager) -> None:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Apoyos"
    sheet.append(["Tipo", "Método de cálculo", "Valor Proveedor", "Referencia JDH", "Fecha Inicio"])
    sheet.append(["tactico_cliente", "valor_unidad", 250.5, 1128.0, date(2026, 10, 1)])
    buffer = io.BytesIO()
    book.save(buffer)

    report = await load(
        bulk, manager, buffer.getvalue(), name="apoyos.xlsx", supplier_code="233", apply=True
    )

    assert report.applied and report.sheet == "Apoyos"
    saved = next(iter(repo.items.values())).data
    assert saved.supplier_value == Decimal("250.5") and saved.product_sku == "1128"
    assert saved.valid_from == date(2026, 10, 1)


async def test_unrecognized_files_and_limits_are_refused(bulk, manager) -> None:
    with pytest.raises(InvalidInputError, match="No se reconocieron las columnas"):
        await load(bulk, manager, "foo,bar\n1,2\n")
    with pytest.raises(InvalidInputError, match="sin filas|no tiene filas"):
        await load(bulk, manager, GENERAL.splitlines()[0] + "\n")
    with pytest.raises(InvalidInputError, match="Formato no soportado"):
        await load(bulk, manager, "x", name="apoyos.pdf")
    with pytest.raises(InvalidInputError, match="máximo por carga"):
        rows = "\n".join([GENERAL.splitlines()[1]] * 51)
        await load(bulk, manager, GENERAL.splitlines()[0] + "\n" + rows)


async def test_only_managers_can_load(bulk) -> None:
    with pytest.raises(ForbiddenError):
        await load(bulk, person("otro@jdh.com.co"), GENERAL)


async def test_profile_defaults_are_validated_and_versioned(profiles, manager) -> None:
    with pytest.raises(InvalidInputError, match="no puede ser un valor por defecto"):
        await profiles.save(
            manager,
            "233",
            defaults={"customer_nit": "1"},
            column_map={},
            sheet=None,
            notes=None,
            expected_version=None,
        )
    with pytest.raises(InvalidInputError, match="calc_method inválido"):
        await profiles.save(
            manager,
            "233",
            defaults={"calc_method": "magia"},
            column_map={},
            sheet=None,
            notes=None,
            expected_version=None,
        )
    with pytest.raises(InvalidInputError, match="Campo desconocido"):
        await profiles.save(
            manager,
            "233",
            defaults={},
            column_map={"nope": ["X"]},
            sheet=None,
            notes=None,
            expected_version=None,
        )

    created = await profiles.save(
        manager,
        "233",
        defaults={"jdh_pct": "2%", "channels": "hoteles, clubes"},
        column_map={"supplier_value": "% APOYO"},
        sheet=None,
        notes=None,
        expected_version=None,
    )
    assert created.version == 1
    assert created.data.defaults == {"jdh_pct": "0.02", "channels": ["HOTELES", "CLUBES"]}
    assert created.data.column_map == {"supplier_value": ["% APOYO"]}
    with pytest.raises(ConflictError):  # already exists: edit it
        await profiles.save(
            manager,
            "233",
            defaults={},
            column_map={},
            sheet=None,
            notes=None,
            expected_version=None,
        )
    updated = await profiles.save(
        manager, "233", defaults={}, column_map={}, sheet="Hoja1", notes=None, expected_version=1
    )
    assert updated.version == 2 and updated.data.sheet == "Hoja1"
    with pytest.raises(ConflictError):
        await profiles.save(
            manager, "233", defaults={}, column_map={}, sheet=None, notes=None, expected_version=1
        )
    await profiles.delete(manager, "233")
    assert await profiles.find("233") is None


async def test_template_follows_the_profile_layout(profiles, manager) -> None:
    general = [header for _, header in template_headers(None)]
    # The short general template: the key, the type and value; never costs or prices.
    assert "Tipo de apoyo" in general and "Valor apoyo" in general
    assert "NIT Cliente" in general and "SKU JDH" in general and "Fecha Inicio" in general
    assert "Costo Referencia" not in general and "Precio Lista Referencia" not in general
    # The short template: NIT, SKUs, type, value, supported margin, dates and notes.
    assert general == [
        "NIT Cliente",
        "SKU JDH",
        "SKU Proveedor",
        "Tipo de apoyo",
        "Valor apoyo",
        "Margen apoyado",
        "Fecha Inicio",
        "Fecha Fin",
        "Observaciones",
    ]
    full = [header for _, header in template_headers(None, full=True)]
    assert "Costo Referencia" in full and "Clase de apoyo" in full

    profile = await profiles.save(
        manager,
        "233",
        defaults={"kind": "rebate"},
        column_map={"supplier_value": ["% APOYO"]},
        sheet=None,
        notes=None,
        expected_version=None,
    )
    own = dict(template_headers(profile))

    assert "kind" not in own and own["supplier_value"] == "% APOYO"
