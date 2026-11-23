"""Reading margin cells and files, and which margin each channel uses."""

import io
from decimal import Decimal
from uuid import uuid4

import openpyxl
import pytest

from app.core.errors import InvalidInputError
from app.modules.commercial_masters.domain.entities import Product, ProductMargins
from app.modules.commercial_masters.domain.margins import (
    channel_field,
    customer_margin_field,
    margin_for,
    parse_margin,
)
from app.modules.commercial_masters.infrastructure.margins_file import (
    SpreadsheetMarginReader,
    parse_tax,
    rows_from_sheet,
    template_workbook,
)

D = Decimal


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.18, (True, D("0.18"))),
        ("0.18", (True, D("0.18"))),
        ("0,18", (True, D("0.18"))),
        (18, (True, D("0.18"))),
        ("18 %", (True, D("0.18"))),
        ("18,5%", (True, D("0.185"))),
        (1, (True, D("0.01"))),
        (0, (True, None)),
        ("0", (True, None)),
        (None, (False, None)),
        ("", (False, None)),
        ("   ", (False, None)),
    ],
)
def test_parse_margin(value, expected) -> None:
    assert parse_margin(value) == expected


@pytest.mark.parametrize("value", ["n/a", -0.1, 101, True, "1,2,3"])
def test_unreadable_margins_are_rejected(value) -> None:
    with pytest.raises(ValueError):
        parse_margin(value)


@pytest.mark.parametrize(
    ("channel", "field"),
    [
        ("MAYORISTAS Y DISTRIBUIDORES", "margin_wholesale"),
        ("Subdistribuidor", "margin_wholesale"),
        ("SUBDISTRIBUIDOR", "margin_wholesale"),
        ("SALSAMENTARIAS", "margin_wholesale"),
        ("Salsamentaría", "margin_wholesale"),
        ("HOTELES Y CLUBES", "margin_list"),
        ("RESTAURANTES", "margin_list"),
        ("AUTOSERVICIO", "margin_list"),
        ("", "margin_list"),
        (None, "margin_list"),
    ],
)
def test_channel_field(channel, field) -> None:
    assert channel_field(channel) == field


@pytest.mark.parametrize(
    ("channel", "center", "team", "field"),
    [
        ("RESTAURANTES", "200", "MASIVO FOOD", "margin_market"),
        ("MAYORISTAS Y DISTRIBUIDORES", "200", None, "margin_market"),
        ("RESTAURANTES", "201", None, "margin_list"),  # Market Valledupar is not Apartadó
        ("AUTOSERVICIO", "003", "MASIVO FOOD", "margin_mass_food"),
        ("SALSAMENTARIAS", "001", "SENIOR", "margin_wholesale"),
        ("RESTAURANTES", "001", None, "margin_list"),
        (None, None, None, "margin_list"),
    ],
)
def test_customer_margin_field(channel, center, team, field) -> None:
    assert customer_margin_field(channel, center, team) == field


def test_new_lists_fall_back_to_food_service() -> None:
    margins = {"margin_list": D("0.2"), "margin_wholesale": None, "margin_market": None}

    assert margin_for(margins, "margin_market") == D("0.2")
    assert margin_for(margins, "margin_wholesale") is None


HEADER = [
    "Referencia",
    "Desc. item",
    "Margen Lista 1",
    "Margen Mayorista",
    "Margen Masivo Food",
    "Margen Market Apartadó",
    "Margen Salsamentarias",
    "Observaciones",
    "Llave",
]


def test_rows_follow_the_master_layout_and_ignore_other_columns() -> None:
    rows = rows_from_sheet(
        [
            HEADER,
            [3144, "Aragan", 0.2, 0.13, 0.2, 0.2, 0.18, 0, "x"],
            ["7244", "Barquillo", 0, 0, 0, 0, 0, "Aseo 20% ", None],
            [None, "sin referencia", 0.2, None, None, None, None, None, None],
        ]
    )

    assert [row.sku for row in rows] == ["3144", "7244"]
    first, second = rows
    assert first.row_number == 2 and first.values["margin_list"] == D("0.2")
    # The Salsamentarias margin was removed (2026-11-23): its old column is not read.
    assert "margin_salsamentarias" not in first.values
    # The master writes 0 where there is no note, and 0 where there is no margin.
    assert first.has_notes is False and first.notes is None
    assert second.values == dict.fromkeys(
        ("margin_list", "margin_wholesale", "margin_mass_food", "margin_market")
    )
    assert second.notes == "Aseo 20%" and second.has_notes is True


def test_an_old_file_with_the_salsamentarias_column_is_read_without_issues() -> None:
    content = workbook(
        {
            "Margen Producto": [
                ["Referencia", "Margen Lista 1", "Margen Salsamentarias", "Observaciones"],
                ["7244", 0.2, "n/a", None],
                ["1569", None, 0.18, None],
            ]
        }
    )

    rows = SpreadsheetMarginReader().read("Maestro Margen.xlsx", content)

    assert [(row.sku, row.values, row.problems) for row in rows] == [
        ("7244", {"margin_list": D("0.2")}, ()),
        ("1569", {}, ()),
    ]


def test_blank_cells_leave_the_margin_and_bad_cells_flag_the_row() -> None:
    rows = rows_from_sheet(
        [["SKU", "Margen Lista", "Margen Mayoristas"], ["1", None, "n/a"], ["2", "20%", None]]
    )

    assert rows[0].values == {} and rows[0].problems == ("Margen Mayoristas inválido: 'n/a'",)
    assert rows[1].values == {"margin_list": D("0.2")} and rows[1].problems == ()


def test_the_header_may_sit_below_a_title_and_needs_a_margin_column() -> None:
    rows = rows_from_sheet([["Margen de productos"], HEADER[:3], ["1", "x", 0.2]])
    assert [row.sku for row in rows] == ["1"] and rows[0].row_number == 3

    with pytest.raises(InvalidInputError, match="Referencia"):
        rows_from_sheet([["Referencia", "Desc. item"], ["1", "x"]])
    with pytest.raises(InvalidInputError, match="Referencia"):
        rows_from_sheet([["Margen Lista 1"], [0.2]])


def workbook(sheets: dict[str, list[list]]) -> bytes:
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_the_margin_master_sheet_wins_over_older_working_copies() -> None:
    content = workbook(
        {
            "Maestro Productos": [["Referencia", "Desc. item"], ["9", "x"]],
            "Margen Productos": [HEADER, ["1", "old", 0.1, None, None, None, None, None, None]],
            "Margen Producto ": [HEADER, ["2", "new", 0.2, None, None, None, None, None, None]],
        }
    )

    reader = SpreadsheetMarginReader()

    assert [row.sku for row in reader.read("Maestro Margen.xlsx", content)] == ["2"]
    assert [row.sku for row in reader.read("m.xlsx", content, "Margen Productos")] == ["1"]
    with pytest.raises(InvalidInputError, match="no tiene la hoja"):
        reader.read("m.xlsx", content, "Otra")


def test_csv_and_unsupported_files() -> None:
    reader = SpreadsheetMarginReader()
    text = "Referencia;Margen Lista 1;Observaciones\n7244;18;ok\n".encode("cp1252")

    [row] = reader.read("m.csv", text)

    assert row.sku == "7244" and row.values == {"margin_list": D("0.18")} and row.notes == "ok"
    with pytest.raises(InvalidInputError, match="Formato"):
        reader.read("m.xls", b"x")
    with pytest.raises(InvalidInputError, match="Excel válido"):
        reader.read("m.xlsx", b"not a zip")


def _master_workbook() -> bytes:
    """The margin master's layout: product sheet with IMPTOLIC and the IMPUESTOS table."""

    book = openpyxl.Workbook()
    products = book.active
    products.title = "Margen Producto "
    products.append(["Referencia", "Desc. item", "Estado", "IMPTOLIC", "Margen Lista 1"])
    products.append([18973, "Juniper Moscow Mule", "Activo", 4036, 0.28])
    products.append([12013, "Vino Cavic", "Activo", 0, 0.57])
    products.append([3144, "Aragan", "Activo", 0, 0.2])
    taxes = book.create_sheet("Margen Proveedor-Impuestos")
    taxes.append(["ESTRUCTURA DE MARGEN", *[None] * 7, "IMPUESTOS"])
    taxes.append(
        [
            "Proveedor",
            "Margen Food Service",
            "Margen Mayoristas",
            None,
            None,
            None,
            None,
            None,
            "Sku",
            "Descripción",
            "Nacional",
            "Costa",
            "Grupo impositivo",
        ]
    )
    taxes.append(["1 - 3M", 0.2, 0.13, *[None] * 5, 18973, "Juniper", 4036, 4144, "0509"])
    taxes.append([None] * 8 + [12013, "Vino", 6911, 6911, "0503"])
    taxes.append([None] * 8 + [21700, "Juniper x12", 48432, 49728, "0509"])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_the_master_carries_the_liquor_tax_by_regional() -> None:
    rows = SpreadsheetMarginReader().read("Maestro Margen.xlsx", _master_workbook())

    by_sku = {row.sku: row for row in rows}
    assert by_sku["18973"].taxes == {
        "liquor_tax": D("4036"),
        "liquor_tax_coast": D("4144"),
        "liquor_tax_group": "0509",
    }
    # The IMPUESTOS table wins over IMPTOLIC 0.
    assert by_sku["12013"].taxes["liquor_tax"] == D("6911")
    # IMPTOLIC 0 with no row in the table clears every liquor tax field.
    assert by_sku["3144"].taxes == {
        "liquor_tax": None,
        "liquor_tax_coast": None,
        "liquor_tax_group": None,
    }
    # A SKU only in the table becomes a row of its own (no margins).
    assert by_sku["21700"].values == {} and by_sku["21700"].taxes["liquor_tax_coast"] == D("49728")
    # The supplier block of the tax sheet is not read as product margins.
    assert "1 - 3M" not in by_sku


def test_the_template_follows_the_master_layout() -> None:
    item = Product(
        id=uuid4(),
        sku="18973",
        supplier_sku=None,
        description="Juniper",
        state="Activo",
        is_active=True,
        commercial_supplier_code="1",
        manufacturer=None,
        brand=None,
        category=None,
        subcategory=None,
        product_line=None,
        pack_factor=None,
        inventory_unit="UNID",
        main_barcode=None,
        margins=ProductMargins(list=D("0.28")),
        liquor_tax=D("4036"),
        liquor_tax_coast=D("4144"),
        liquor_tax_group="0509",
    )

    content = template_workbook([item])

    rows = SpreadsheetMarginReader().read("plantilla.xlsx", content)
    assert rows[0].values["margin_list"] == D("0.28")
    assert rows[0].taxes["liquor_tax_coast"] == D("4144")
    book = openpyxl.load_workbook(io.BytesIO(content))
    assert book.sheetnames == ["Margen Producto", "Margen Proveedor-Impuestos", "Instrucciones"]
    header = [cell.value for cell in book["Margen Producto"][1]]
    assert "Margen Salsamentarias" not in header and header[-2] == "Margen Market Apartadó"
    assert book["Margen Proveedor-Impuestos"]["I2"].value == "Sku"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (4036, (True, D("4036"))),
        ("6.911,00", (True, D("6911.00"))),
        (0, (True, None)),
        (None, (False, None)),
    ],
)
def test_parse_tax(value, expected) -> None:
    assert parse_tax(value) == expected
