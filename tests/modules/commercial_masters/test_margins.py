"""Reading margin cells and files, and which margin each channel uses."""

import io
from decimal import Decimal

import openpyxl
import pytest
from app.core.errors import InvalidInputError
from app.modules.commercial_masters.domain.margins import channel_field, parse_margin
from app.modules.commercial_masters.infrastructure.margins_file import (
    SpreadsheetMarginReader,
    rows_from_sheet,
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
        ("SALSAMENTARIAS", "margin_salsamentarias"),
        ("Salsamentaría", "margin_salsamentarias"),
        ("HOTELES Y CLUBES", "margin_list"),
        ("RESTAURANTES", "margin_list"),
        ("AUTOSERVICIO", "margin_list"),
        ("", "margin_list"),
        (None, "margin_list"),
    ],
)
def test_channel_field(channel, field) -> None:
    assert channel_field(channel) == field


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
    assert first.values["margin_salsamentarias"] == D("0.18")
    # The master writes 0 where there is no note, and 0 where there is no margin.
    assert first.has_notes is False and first.notes is None
    assert second.values == dict.fromkeys(
        (
            "margin_list",
            "margin_wholesale",
            "margin_mass_food",
            "margin_market",
            "margin_salsamentarias",
        )
    )
    assert second.notes == "Aseo 20%" and second.has_notes is True


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
