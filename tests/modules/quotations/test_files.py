"""The SIESA import file and the update workbook."""

import io
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

from app.core.errors import InvalidInputError
from app.modules.quotations.domain.export import (
    ExportRow,
    ExportTaxRow,
    NewPriceList,
    PriceListExport,
)
from app.modules.quotations.infrastructure.siesa_file import SiesaPriceFileWriter, money
from app.modules.quotations.infrastructure.workbook import (
    PRICE_HEADERS,
    TAX_HEADERS,
    PriceListWorkbookWriter,
)

GOLDEN = Path(__file__).parent / "fixtures" / "precios-golden.txt"
DAY = date(2026, 1, 1)


def sample() -> PriceListExport:
    """The update behind the file SIESA's Excel importer produced (193257H10M2…txt)."""

    return PriceListExport(
        rows=(ExportRow("1", "123", DAY, "unid", Decimal("100")),),
        taxes=(ExportTaxRow("1", "123", DAY, "unid", "0011", Decimal("10")),),
    )


def test_reproduces_the_file_of_siesas_importer_byte_for_byte() -> None:
    content = SiesaPriceFileWriter().write(sample())

    assert content == GOLDEN.read_bytes()
    assert [len(line) for line in content.split(b"\r\n")] == [18, 254, 175, 18, 0]


def test_numbers_every_record_and_keeps_each_tax_after_its_price() -> None:
    export = PriceListExport(
        rows=(
            ExportRow("10U", "26278", DAY, "UNID", Decimal("88209.5")),
            ExportRow("10U", "2537", DAY, "UNID", Decimal("4650")),
        ),
        taxes=(ExportTaxRow("10U", "26278", DAY, "UNID", "0024", Decimal("42451")),),
    )

    lines = SiesaPriceFileWriter().write(export).decode("ascii").split("\r\n")[:-1]

    assert [line[:11] for line in lines] == [
        "00000010000",
        "00000020126",
        "00000030127",
        "00000040126",
        "00000059999",
    ]
    assert lines[1][19:22] == "10U" and lines[1][29:34] == "26278"
    assert lines[1][167:187] == "000000000088209.5000"
    assert lines[2][151:155] == "0024" and lines[2][155:] == "000000000042451.0000"


def test_rejects_what_does_not_fit() -> None:
    writer = SiesaPriceFileWriter()

    with pytest.raises(InvalidInputError):
        writer.write(PriceListExport((ExportRow("1", "Ñ1", DAY, "UNID", Decimal(1)),)))
    with pytest.raises(InvalidInputError):
        writer.write(PriceListExport((ExportRow("1234", "1", DAY, "UNID", Decimal(1)),)))
    with pytest.raises(InvalidInputError):
        money(Decimal(10) ** 15)
    assert money(Decimal("3323.36444")) == "000000000003323.3644"


def test_workbook_has_both_sheets_of_the_template() -> None:
    content = PriceListWorkbookWriter().write(sample())

    workbook = load_workbook(io.BytesIO(content))
    assert workbook.sheetnames == ["Precios", "Impuestos en valor"]
    prices = list(workbook["Precios"].iter_rows(values_only=True))
    taxes = list(workbook["Impuestos en valor"].iter_rows(values_only=True))
    # The date is the whole number AAAAMMDD with format 0, the reference a number.
    assert prices == [PRICE_HEADERS, ("1", 123, 20260101, "unid", 100)]
    assert taxes == [TAX_HEADERS, ("1", 123, 20260101, "unid", "0011", 10)]
    cell = workbook["Precios"]["C2"]
    assert cell.number_format == "0" and cell.font.name == "Calibri Light"
    assert cell.font.size == 10


def test_a_new_list_goes_first_as_record_0112() -> None:
    export = PriceListExport(
        rows=(ExportRow("C01", "22858", date(2026, 11, 4), "UNID", Decimal("53958")),),
        new_list=NewPriceList("C01", "COTIZADOR DISTRI CARPO FOODS"),
    )

    lines = SiesaPriceFileWriter().write(export).decode("ascii").split("\r\n")

    # The header record as the importer's dictionary lays it out (t662_imp_campos).
    assert lines[1] == (
        "0000002011200010010C01COTIZADOR DISTRI CARPO FOODS            COP100   "
        "000000000000000.00000000.00000"
    )
    assert [len(line) for line in lines] == [18, 101, 254, 18, 0]
    assert lines[2][7:11] == "0126" and lines[2][19:22] == "C01"
