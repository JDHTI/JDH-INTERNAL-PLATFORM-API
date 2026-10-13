"""Reading the SIESA retained orders report."""

import io
from datetime import date, datetime
from decimal import Decimal

import openpyxl
import pytest

from app.core.errors import InvalidInputError
from app.modules.order_validation.infrastructure.order_file import (
    SpreadsheetOrderReader,
    parse_decimal,
)

HEADER = [
    "C.O. factura",
    "Fecha",
    "Nro documento",
    "Estado docto.",
    "Cliente factura",
    "Razon social cliente factura",
    "Nombre vendedor cliente",
    "CANAL",
    "Bodega",
    "Referencia",
    "Desc. item",
    "Precio unit.",
    "Cant. pedida",
    "Margen promedio",
    "PROVEEDOR",
    "Lista de precios fact",
    "Retenido margen",
]
ROW = [
    "004",
    datetime(2026, 10, 9),
    "PV-00280886",
    "Retenido",
    "900315138      ",
    "PAVILLION HOTELS GROUP SAS",
    "FLOREZ MERCADO SUSAN PAOLA",
    "HOTELES Y CLUBES",
    "CTG14",
    18294,
    "Tenedor Entra Eleganc SCU049020512I C12",
    118628,
    24,
    2.2212,
    "359 - LOCERIA COLOMBIANA S.A.",
    "58B",
    "Si",
]


def workbook(*rows, title: str | None = None) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    if title:
        sheet.append([title])
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def read(filename: str, content: bytes):
    return SpreadsheetOrderReader().read(filename, content)


def test_reads_the_report_as_exported() -> None:
    [line] = read("Pedidos Retenidos.xlsx", workbook(HEADER, ROW))

    assert line.row_number == 2 and line.document == "PV-00280886"
    assert line.customer_nit == "900315138" and line.sku == "18294"
    assert line.supplier_code == "359" and line.supplier_name == "LOCERIA COLOMBIANA S.A."
    assert line.unit_price == Decimal("118628") and line.quantity == Decimal("24")
    # Percentage points in the report, a fraction in the line.
    assert line.margin_pct == Decimal("0.022212")
    assert line.order_date == date(2026, 10, 9) and line.operation_center == "004"
    assert line.channel == "HOTELES Y CLUBES" and line.warehouse == "CTG14"
    assert line.price_list == "58B" and line.retained == "Si" and line.problems == ()


def test_the_header_may_sit_below_a_title_and_empty_rows_are_skipped() -> None:
    lines = read("x.xlsx", workbook(HEADER, ROW, [None] * len(HEADER), ROW, title="Reporte"))

    assert [line.row_number for line in lines] == [3, 5]


def test_columns_are_found_by_name_in_any_order() -> None:
    order = [HEADER.index(name) for name in reversed(HEADER)]
    [line] = read("x.xlsx", workbook([HEADER[i] for i in order], [ROW[i] for i in order]))

    assert line.sku == "18294" and line.margin_pct == Decimal("0.022212")


def test_csv_with_semicolons_and_colombian_numbers() -> None:
    text = (
        "Nro documento;Cliente factura;Referencia;Precio unit.;Margen promedio;Proveedor;Fecha\n"
        'PV-1;900315138;18294;"118.628,50";-11,1128;359 - LOCERIA;09/10/2026\n'
    )

    [line] = read("pedidos.csv", text.encode("cp1252"))

    assert line.unit_price == Decimal("118628.50")
    assert line.margin_pct == Decimal("-0.111128")
    assert line.order_date == date(2026, 10, 9)


def test_a_bad_cell_keeps_the_row_and_records_the_problem() -> None:
    bad = list(ROW)
    bad[13] = "n/a"

    [line] = read("x.xlsx", workbook(HEADER, bad))

    assert line.margin_pct is None and line.problems == ("Margen ilegible: 'n/a'",)


def test_missing_required_columns_are_named() -> None:
    header = [name for name in HEADER if name not in ("Margen promedio", "PROVEEDOR")]

    with pytest.raises(InvalidInputError, match="Margen promedio, Proveedor"):
        read("x.xlsx", workbook(header))


def test_files_that_are_not_the_report() -> None:
    with pytest.raises(InvalidInputError, match="Formato no soportado"):
        read("pedidos.xls", b"whatever")
    with pytest.raises(InvalidInputError, match="no es un libro de Excel"):
        read("pedidos.xlsx", b"not a zip")
    with pytest.raises(InvalidInputError, match="encabezados"):
        read("pedidos.xlsx", workbook(["a", "b"], [1, 2]))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (2.2212, "2.2212"),
        ("1.234,5", "1234.5"),
        ("1,234.5", "1234.5"),
        ("118,628", "118628"),
        ("12,5%", "12.5"),
        ("$ 1.234.567", "1234567"),
        ("", None),
    ],
)
def test_parse_decimal(value, expected) -> None:
    result = parse_decimal(value)

    assert result == (None if expected is None else Decimal(expected))


def test_the_liquor_tax_column_is_read_per_line() -> None:
    header = [*HEADER[:16], "Vlr. imp. IMPTOLIC", HEADER[16]]
    wine = [*ROW[:16], 6911, ROW[16]]
    plain = [*ROW[:16], 0, ROW[16]]
    blank = [*ROW[:16], None, ROW[16]]

    lines = read("Pedidos Retenidos 2.xlsx", workbook(header, wine, plain, blank))

    assert [line.liquor_tax_value for line in lines] == [Decimal("6911"), Decimal("0"), None]
    assert all(line.retained == "Si" for line in lines)


def test_files_without_the_liquor_tax_column_still_read() -> None:
    [line] = read("Pedidos Retenidos.xlsx", workbook(HEADER, ROW))

    assert line.liquor_tax_value is None
