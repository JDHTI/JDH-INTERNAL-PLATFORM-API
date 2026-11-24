"""Supplier increases: the MAESTRA workbook, the review and the history of increases."""

from __future__ import annotations

import io
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import openpyxl
import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.costs.application.access import CostAccess
from app.modules.costs.application.increases import IncreaseService, LineChanges
from app.modules.costs.application.novelties import UploadedFile
from app.modules.costs.domain.increases import (
    APPROVED,
    NATIONAL,
    REGIONAL_NAMES,
    REJECTED,
    REST,
    SCOPES,
    LineInput,
    as_fraction,
    build_line,
    clean_scope,
    covered_regionals,
    list_price,
    scope_problems,
    with_tax,
)
from app.modules.costs.infrastructure.increase_file import (
    COLUMNS,
    FIRST_ROW,
    HEADER_ROW,
    SHEET,
    IncreaseWorkbookReader,
    template_workbook,
    workbook_name,
)
from app.modules.costs.infrastructure.ledger import ledger_values
from app.modules.identity.domain.principal import Principal
from tests.modules.costs.fakes import NOW, TODAY, FakeGuard, FakePolicy, FakeStorage
from tests.modules.costs.increase_fakes import (
    FakeIncreaseRepository,
    FakeItems,
    FakeLedger,
    item,
)

D = Decimal
PERSON = Principal(uuid4(), "compras@jdh.com.co", "Compras", False)
COLUMN = {column.header: position for position, column in enumerate(COLUMNS, start=1)}


class Reasons:
    async def __call__(self):
        return []


def service(
    items=None, *, manage: bool = True, ledger=None, repository=None, storage=None
) -> IncreaseService:
    return IncreaseService(
        repository or FakeIncreaseRepository(),
        items or FakeItems(item("10042", "22039.88"), item("9894", "56962.04", icui="0.2")),
        ledger or FakeLedger({"10042": D("0.18")}),
        CostAccess(FakePolicy(), FakePolicy(), FakePolicy(manage=manage), FakePolicy()),
        storage or FakeStorage(),
        IncreaseWorkbookReader(),
        template_workbook,
        workbook_name,
        Reasons(),
        max_upload_bytes=1024 * 1024,
        today=lambda: TODAY,
        clock=lambda: NOW,
        support_guard=FakeGuard(),
    )


def filled(content: bytes, rows: dict[str, dict[str, object]]) -> bytes:
    """The template with some rows filled the way a supplier would, by reference."""

    book = openpyxl.load_workbook(io.BytesIO(content))
    sheet = book[SHEET]
    for row in range(FIRST_ROW, sheet.max_row + 1):
        sku = sheet.cell(row, COLUMN["Referencia"]).value
        for header, value in rows.get(sku, {}).items():
            sheet.cell(row, COLUMN[header], value)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


# --- Rules ---


def test_costs_with_tax_and_variation_follow_the_workbook() -> None:
    assert with_tax(D("1000"), D("0.2"), D("50")) == D("1250.0")
    assert with_tax(D("1000"), None, None) == D("1000")
    assert as_fraction(D("20")) == D("0.2") and as_fraction(D("0.2")) == D("0.2")
    assert list_price(D("8700"), D("0.13")) == D("10000.00")
    assert list_price(D("8700"), None) is None and list_price(D("1"), D("1")) is None

    line = build_line(uuid4(), item("9894", "1000", icui="0.2"), LineInput("9894", D("1100")))

    assert (line.old_cost_with_tax, line.new_cost_with_tax) == (D("1200.0"), D("1320.0"))
    assert line.variation_amount == D("120.0") and line.variation_pct == D("0.1")
    assert line.change == "subio"
    assert line.regional == NATIONAL and line.public_price is None


def test_the_supplier_icui_wins() -> None:
    values = LineInput("10042", D("900"), icui_pct=D("0"), regular_discount_pct=D("0.03"))

    line = build_line(uuid4(), item("10042", "1000", icui="0.2"), values)

    assert line.icui_pct == D("0") and line.change == "bajo"
    assert line.regular_discount_pct == D("0.03")


def test_the_regional_is_read_loosely() -> None:
    assert clean_scope(None) == NATIONAL and clean_scope(" ") == NATIONAL
    assert clean_scope("Bogotá") == "BOGOTA" and clean_scope("resto") == REST
    assert clean_scope("resto de  regionales") == REST and clean_scope("COSTA") is None


def test_rest_covers_the_regionals_the_other_rows_do_not_name() -> None:
    assert covered_regionals(NATIONAL, []) == REGIONAL_NAMES
    assert covered_regionals("ANTIOQUIA", [REST]) == ("ANTIOQUIA",)
    assert covered_regionals(REST, ["ANTIOQUIA", "CARTAGENA"]) == (
        "BOGOTA",
        "PEREIRA",
        "BUCARAMANGA",
        "BARRANQUILLA",
    )


def test_regional_rows_of_a_reference_are_checked_together() -> None:
    rows = [
        LineInput("1", D("1"), regional="ANTIOQUIA"),
        LineInput("1", D("1"), regional=REST),  # fine: Antioquia apart, the rest together
        LineInput("2", D("1"), regional=NATIONAL),
        LineInput("2", D("1"), regional="BOGOTA"),  # NACIONAL with another row
        LineInput("3", D("1"), regional=REST),  # RESTO alone
        LineInput("4", D("1"), regional="PEREIRA"),
        LineInput("4", D("1"), regional="PEREIRA"),  # repeated
        *(LineInput("5", D("1"), regional=name) for name in REGIONAL_NAMES),
        LineInput("5", D("1"), regional=REST),  # nothing left to cover
    ]

    problems = scope_problems(rows)

    assert sorted(problems) == [2, 4, 6, len(rows) - 1]
    assert "NACIONAL no se combina" in problems[2][0]
    assert "necesita otra fila" in problems[4][0]
    assert problems[6] == ["La regional PEREIRA está repetida en la referencia"]
    assert "ya están las seis" in problems[len(rows) - 1][0]


def test_the_workbook_is_named_as_the_area_does() -> None:
    assert (
        workbook_name("50", "ASCENDER S.A.", date(2026, 11, 23))
        == "MAESTRA 50 - ASCENDER S.A. 23-11-2026.xlsx"
    )
    assert (
        workbook_name("8", 'TEAM/FOODS "X"', date(2026, 1, 5))
        == "MAESTRA 8 - TEAM FOODS X 05-01-2026.xlsx"
    )


# --- The workbook ---


def test_the_template_keeps_the_maestra_layout() -> None:
    content = template_workbook(
        [item("10042", "22039.88"), item("9894", "56962.04", icui="0.2")], ["OBSEQUIO"], TODAY
    )

    book = openpyxl.load_workbook(io.BytesIO(content))
    sheet = book[SHEET]
    assert book.sheetnames == [SHEET, "Motivos"] and book["Motivos"].sheet_state == "hidden"
    assert sheet["C1"].value == "SISTEMA INTEGRADO DE GESTIÓN"
    assert sheet["T1"].value == "Código: P.17.F.04" and sheet["T3"].value == "Fecha: 23/11/2026"
    assert sheet.freeze_panes == "A9" and len(sheet._images) == 1
    headers = [sheet.cell(HEADER_ROW, column).value for column in range(1, len(COLUMNS) + 1)]
    assert headers[:15] == [
        "Fecha de Incremento",
        "SKU Proveedor",
        "Codigo de Barras EAN 13",
        "Nit",
        "Referencia",
        "Proveedor",
        "Desc. item",
        "Unidad de Empaque",
        "Estado el Item",
        "Categoria",
        "Grupo Impositivo",
        "Descuento Regular",
        "Regional",
        "Precio Venta Público sin IVA (PVP)",
        "ICUI (Indique el porcentaje)",
    ]
    assert headers[-1] == "Estado " and len(headers) == 25
    lists = {
        str(rule.sqref).split(":")[0]: rule.formula1
        for rule in sheet.data_validations.dataValidation
    }
    assert lists[f"M{FIRST_ROW}"] == f'"{",".join(SCOPES)}"'
    second = FIRST_ROW + 1
    assert sheet.cell(second, COLUMN["Regional"]).value == NATIONAL
    assert sheet.cell(second, COLUMN["Referencia"]).value == "9894"
    assert sheet.cell(second, COLUMN["ICUI (Indique el porcentaje)"]).value == 0.2
    assert sheet.cell(second, COLUMN["Costo anterior sin Impuesto Saludable"]).value == 56962.04
    assert sheet.cell(second, COLUMN["Estado Incremento"]).value.startswith("=IF(")
    assert sheet.cell(FIRST_ROW, COLUMN["SKU Proveedor"]).number_format == "@"


def test_a_returned_workbook_reads_only_the_rows_with_a_new_cost() -> None:
    content = template_workbook([item("10042"), item("9894"), item("1001")], [], TODAY)
    returned = filled(
        content,
        {
            "10042": {
                "Costo Nuevo sin Impuesto Saludable": 1100,
                "Fecha de Incremento": datetime(2026, 12, 1),
                "Descuento Regular": 3,  # typed as points
                "Regional": "Antioquia",
                "Precio Venta Público sin IVA (PVP)": 1990.5,
                "OBSERVACIONES": "duplicado",
            },
            "9894": {
                "Costo Nuevo sin Impuesto Saludable": "$ 1.250,50",
                "Fecha de Incremento": "01/12/2026",
                "Regional": None,  # blank is NACIONAL
                "Estado ": "NO",
            },
        },
    )

    rows = IncreaseWorkbookReader().read("MAESTRA 50.xlsx", returned)

    assert rows.rows_read == 3 and rows.issues == ()
    first, second = rows.lines
    assert (first.sku, first.new_cost, first.increased_on) == (
        "10042",
        D("1100"),
        date(2026, 12, 1),
    )
    assert first.regular_discount_pct == D("0.03")
    assert (first.regional, first.public_price) == ("ANTIOQUIA", D("1990.50"))
    assert first.observation == "DUPLICADO" and first.accepted
    assert (second.new_cost, second.accepted, second.regional) == (D("1250.50"), False, NATIONAL)


def test_row_problems_are_reported_by_sheet_row() -> None:
    content = template_workbook([item("10042"), item("9894")], [], TODAY)
    returned = filled(
        content,
        {
            "10042": {"Costo Nuevo sin Impuesto Saludable": 1100},  # no date
            "9894": {
                "Costo Nuevo sin Impuesto Saludable": 1100,
                "Fecha de Incremento": datetime(2026, 12, 1),
                "Regional": "COSTA",
                "Precio Venta Público sin IVA (PVP)": "mucho",
            },
        },
    )

    rows = IncreaseWorkbookReader().read("m.xlsx", returned)

    assert [(issue.row_number, issue.problem) for issue in rows.issues] == [
        (FIRST_ROW, "Falta la fecha de incremento"),
        (FIRST_ROW + 1, "El PVP sin IVA no es un número"),
        (FIRST_ROW + 1, "Regional desconocida: COSTA; elija una de la lista"),
    ]
    assert rows.lines == ()


def test_a_file_without_the_columns_is_refused() -> None:
    book = openpyxl.Workbook()
    book.active.append(["Codigo", "Precio"])
    buffer = io.BytesIO()
    book.save(buffer)

    with pytest.raises(InvalidInputError, match="columnas obligatorias"):
        IncreaseWorkbookReader().read("x.xlsx", buffer.getvalue())
    with pytest.raises(InvalidInputError):
        IncreaseWorkbookReader().read("x.csv", b"a,b")


# --- The service ---


async def test_the_template_carries_only_quoted_products() -> None:
    increases = service(FakeItems(item("10042"), item("23838", old_cost=None)))

    name, content = await increases.template("50")

    assert name == "MAESTRA 50 - ASCENDER S.A. 23-11-2026.xlsx"
    sheet = openpyxl.load_workbook(io.BytesIO(content))[SHEET]
    assert sheet.cell(FIRST_ROW, COLUMN["Referencia"]).value == "10042"
    assert sheet.cell(FIRST_ROW + 1, COLUMN["Referencia"]).value is None
    with pytest.raises(NotFoundError):
        await increases.template("999")


async def test_upload_simulates_then_stores_a_request_in_review() -> None:
    storage = FakeStorage()
    repository = FakeIncreaseRepository()
    increases = service(repository=repository, storage=storage)
    _, content = await increases.template("50")
    returned = filled(
        content,
        {
            "10042": {
                "Costo Nuevo sin Impuesto Saludable": 23000,
                "Fecha de Incremento": datetime(2026, 12, 1),
            }
        },
    )

    simulated = await increases.upload(PERSON, "50", "maestra.xlsx", returned, apply=False)
    assert not simulated.applied and len(simulated.lines) == 1 and repository.requests == {}

    report = await increases.upload(
        PERSON, "50", "maestra.xlsx", returned, apply=True, note="Carta 12"
    )
    request = await increases.get(report.request_id)
    assert (
        request.status == "en_revision"
        and request.source == "plantilla"
        and request.note == "Carta 12"
    )
    assert request.supplier_name == "ASCENDER S.A." and request.line_count == 1
    line = request.lines[0]
    assert line.change == "subio" and line.variation_amount == D("960.12")
    assert list(storage.objects) == [request.storage_key]
    assert (await increases.file(request.id))[1] == returned


PDF = b"%PDF-1.7 carta del proveedor"


async def test_the_supplier_support_goes_with_the_maestra_under_the_area() -> None:
    storage = FakeStorage()
    increases = service(storage=storage)
    _, content = await increases.template("50")
    returned = filled(
        content,
        {
            "10042": {
                "Costo Nuevo sin Impuesto Saludable": 23000,
                "Fecha de Incremento": datetime(2026, 12, 1),
            }
        },
    )
    letter = UploadedFile("Carta alza.pdf", PDF)

    # A simulation checks the type but keeps nothing.
    with pytest.raises(InvalidInputError, match="tipo de archivo"):
        await increases.upload(
            PERSON, "50", "m.xlsx", returned, apply=False, support=UploadedFile("x.exe", b"MZ")
        )
    simulated = await increases.upload(
        PERSON, "50", "m.xlsx", returned, apply=False, support=letter
    )
    assert not simulated.applied and storage.objects == {}

    report = await increases.upload(PERSON, "50", "m.xlsx", returned, apply=True, support=letter)
    request = await increases.get(report.request_id)

    support = request.support
    assert support is not None and support.file_name == "Carta alza.pdf"
    assert support.storage_key.startswith(f"areas/abastecimiento/costos/alzas/{request.id}/")
    assert (support.content_type, support.size_bytes, support.uploaded_by) == (
        "application/pdf",
        len(PDF),
        PERSON.email,
    )
    assert (await increases.support(request.id))[1] == PDF
    assert len(storage.objects) == 2


async def test_a_support_is_attached_replaced_and_removed_while_in_review() -> None:
    storage = FakeStorage()
    increases = service(storage=storage)
    request = await increases.create(
        PERSON,
        "50",
        [LineInput(sku="10042", new_cost=D(23000), increased_on=date(2026, 12, 1))],
    )
    with pytest.raises(NotFoundError):
        await increases.support(request.id)

    first = await increases.attach_support(PERSON, request.id, UploadedFile("carta.pdf", PDF))
    second = await increases.attach_support(PERSON, request.id, UploadedFile("correo.png", b"png"))

    # The replaced file leaves the bucket.
    assert first.support.storage_key not in storage.objects
    assert list(storage.objects) == [second.support.storage_key]
    assert second.support.content_type == "image/png"
    with pytest.raises(InvalidInputError):
        await increases.attach_support(PERSON, request.id, UploadedFile("carta.pdf", b"no pdf"))
    with pytest.raises(InvalidInputError, match="Adjunta"):
        await increases.attach_support(PERSON, request.id, UploadedFile("carta.pdf", b""))
    with pytest.raises(ForbiddenError):
        await service(manage=False).attach_support(
            PERSON, request.id, UploadedFile("carta.pdf", PDF)
        )

    removed = await increases.remove_support(PERSON, request.id)
    assert removed.support is None and storage.objects == {}
    with pytest.raises(NotFoundError):
        await increases.remove_support(PERSON, request.id)

    await increases.attach_support(PERSON, request.id, UploadedFile("carta.pdf", PDF))
    await increases.reject(PERSON, request.id, "Sin carta firmada")
    with pytest.raises(ConflictError):
        await increases.attach_support(PERSON, request.id, UploadedFile("carta.pdf", PDF))
    # A decided request keeps its support for whoever reads it.
    assert (await increases.support(request.id))[1] == PDF


async def test_unknown_or_repeated_references_block_the_upload() -> None:
    increases = service()
    _, content = await increases.template("50")
    book = openpyxl.load_workbook(io.BytesIO(content))
    sheet = book[SHEET]
    for row, sku in ((20, "77777"), (21, "10042"), (22, "10042")):
        sheet.cell(row, COLUMN["Referencia"], sku)
        sheet.cell(row, COLUMN["Costo Nuevo sin Impuesto Saludable"], 1000)
        sheet.cell(row, COLUMN["Fecha de Incremento"], datetime(2026, 12, 1))
    buffer = io.BytesIO()
    book.save(buffer)

    report = await increases.upload(PERSON, "50", "m.xlsx", buffer.getvalue(), apply=True)

    assert not report.applied and report.blocked
    problems = {(issue.row_number, issue.problem) for issue in report.issues}
    assert (20, "La referencia no es un producto activo del proveedor") in problems
    assert (22, "La regional NACIONAL está repetida en la referencia") in problems
    # Rows without a regional are NACIONAL: the first cannot go with another row either.
    assert any(row == 21 and "NACIONAL no se combina" in text for row, text in problems)


async def test_an_untouched_template_is_refused_and_readers_cannot_load() -> None:
    increases = service()
    _, content = await increases.template("50")

    with pytest.raises(InvalidInputError, match="ningún costo nuevo"):
        await increases.upload(PERSON, "50", "m.xlsx", content, apply=False)
    with pytest.raises(ForbiddenError):
        await service(manage=False).upload(PERSON, "50", "m.xlsx", content, apply=False)


async def test_a_manual_request_review_and_approval_write_the_history() -> None:
    ledger = FakeLedger({"10042": D("0.18")})
    increases = service(ledger=ledger)
    request = await increases.create(
        PERSON,
        "50",
        [
            LineInput(
                "10042",
                D("23000"),
                increased_on=date(2026, 12, 1),
                regional="ANTIOQUIA",
                regular_discount_pct=D("0.02"),
                public_price=D("31990"),
            ),
            LineInput("10042", D("22000"), increased_on=date(2026, 12, 1), regional=REST),
            LineInput("9894", D("60000"), increased_on=date(2026, 12, 1)),
        ],
        note="Correo del proveedor",
    )
    assert request.source == "manual" and request.line_count == 3
    rest = next(line for line in request.lines if line.regional == REST)
    assert rest.regionals == ("BOGOTA", "PEREIRA", "BUCARAMANGA", "CARTAGENA", "BARRANQUILLA")

    dropped = next(line for line in request.lines if line.sku == "9894")
    request = await increases.update_line(
        PERSON, request.id, dropped.id, LineChanges(accepted=False)
    )
    antioquia = next(line for line in request.lines if line.regional == "ANTIOQUIA")
    request = await increases.update_line(
        PERSON, request.id, antioquia.id, LineChanges(new_cost=D("22500"))
    )
    assert request.accepted_count == 2

    approved = await increases.approve(PERSON, request.id)

    assert approved.status == APPROVED and approved.decided_by == PERSON.email
    # Antioquia is one history row; the rest, one per regional it covers.
    assert [(entry.regional, entry.new_cost) for entry in ledger.entries] == [
        ("ANTIOQUIA", D("22500")),
        *((name, D("22000")) for name in rest.regionals),
    ]
    entry = ledger.entries[0]
    assert (entry.sku, entry.notified_on) == ("10042", NOW.date())
    assert entry.margin_pct == D("0.18") and entry.price == list_price(D("22500"), D("0.18"))
    assert entry.observations == "Descuento regular 2.00 % · PVP sin IVA 31990.00"
    assert ledger.entries[1].observations == (
        "RESTO DE REGIONALES (BOGOTA, PEREIRA, BUCARAMANGA, CARTAGENA, BARRANQUILLA)"
    )
    values = ledger_values(entry)
    assert values.source == "solicitud" and values.system_updated_on is None and values.notified
    assert values.request_line_id == entry.line_id and values.regional == "ANTIOQUIA"
    assert values.key == "10042|2026-12-01|2026-11-23|ANTIOQUIA"
    assert ledger_values(ledger.entries[1]).key.endswith("|BOGOTA")
    assert all(line.ledger_id is not None for line in approved.lines if line.sku == "10042")
    with pytest.raises(ConflictError):
        await increases.reject(PERSON, request.id, "tarde")


async def test_manual_lines_with_problems_and_rejections() -> None:
    increases = service()

    with pytest.raises(InvalidInputError, match="77777"):
        await increases.create(PERSON, "50", [LineInput("77777", D("1"), increased_on=TODAY)])
    with pytest.raises(InvalidInputError, match="fecha de incremento"):
        await increases.create(PERSON, "50", [LineInput("10042", D("1"))])
    request = await increases.create(PERSON, "50", [LineInput("10042", D("1"), increased_on=TODAY)])
    with pytest.raises(InvalidInputError, match="por qué"):
        await increases.reject(PERSON, request.id, " ")

    rejected = await increases.reject(PERSON, request.id, "Costo sin soporte")

    assert rejected.status == REJECTED and rejected.decision_note == "Costo sin soporte"


async def test_requests_list_by_status_and_supplier() -> None:
    increases = service()
    await increases.create(PERSON, "50", [LineInput("10042", D("1"), increased_on=TODAY)])

    page = await increases.page("en_revision", "50", 1, 20)

    assert page.total == 1 and page.counts == {"en_revision": 1, "aprobada": 0, "rechazada": 0}
    with pytest.raises(InvalidInputError):
        await increases.page("cerrada", None, 1, 20)
