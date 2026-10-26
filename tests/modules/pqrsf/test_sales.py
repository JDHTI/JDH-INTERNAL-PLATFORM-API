from datetime import date

import pytest
from app.modules.pqrsf.domain.sales import invoice_candidates, nit_core
from app.modules.pqrsf.infrastructure.sales_lookup import WarehouseSalesLookup


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("FEV-1386163", ["FEV-1386163"]),
        ("fev 001386163", ["FEV-1386163"]),
        ("FEV.1386163", ["FEV-1386163"]),
        ("1386163", ["FEV-1386163", "FEE-1386163", "NND-1386163"]),
        ("1386163FEV", []),
        ("", []),
    ],
)
def test_a_typed_invoice_becomes_the_numbers_of_the_sales_data(typed, expected) -> None:
    assert invoice_candidates(typed) == expected


def test_nit_ignores_the_verification_digit() -> None:
    assert nit_core("900.123.456-7") == nit_core("900123456") == "900123456"
    assert nit_core(None) == ""


class FakeWarehouse:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.args = None

    async def fetch(self, query, *args):
        self.args = (query, args)
        return [row for row in self.rows if row["nro_transaccion"] in args[0]]


def _row(number, nit, code="V1", name="Carolina", email="carolina@jdh.com.co"):
    return {
        "nro_transaccion": number,
        "nit_cliente": nit,
        "codigo": code,
        "nombre": name,
        "correo": email,
    }


@pytest.mark.asyncio
async def test_the_seller_of_each_invoice_is_found_and_bounded_by_date() -> None:
    warehouse = FakeWarehouse([_row("FEV-1", "900123456"), _row("FEV-2", "800")])
    lookup = WarehouseSalesLookup(warehouse, today=lambda: date(2026, 10, 26))

    found = await lookup.sellers_of(["FEV-9", "FEV-1", "FEV-2"], "900123456-7")

    # FEV-2 belongs to another customer: its seller must not get the case.
    assert [(s.invoice_number, s.name) for s in found] == [("FEV-1", "Carolina")]
    query, args = warehouse.args
    assert '"consumo"."ventas"' in query and "'Anulada'" in query and "'Ventas'" in query
    assert args[1] == date(2024, 10, 26)


@pytest.mark.asyncio
async def test_a_number_of_several_customers_is_resolved_by_nit_or_skipped() -> None:
    rows = [_row("FEV-5", "900123456", name="Del cliente"), _row("FEV-5", "811", name="Otro")]
    lookup = WarehouseSalesLookup(FakeWarehouse(rows))

    assert [s.name for s in await lookup.sellers_of(["FEV-5"], "900123456")] == ["Del cliente"]
    assert await lookup.sellers_of(["FEV-5"], None) == []
    assert await lookup.sellers_of(["FEV-5"], "123") == []


def test_the_schema_must_be_an_identifier() -> None:
    with pytest.raises(ValueError):
        WarehouseSalesLookup(FakeWarehouse([]), schema="consumo; drop")
