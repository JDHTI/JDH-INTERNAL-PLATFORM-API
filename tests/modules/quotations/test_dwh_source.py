"""Warehouse reads of the quoter: bounded, parameterized and mapped."""

from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderUnavailable
from app.modules.quotations.infrastructure.dwh_source import WarehouseQuotationSource


class FakeConnection:
    def __init__(self, rows=(), fail: bool = False) -> None:
        self.rows = list(rows)
        self.fail = fail
        self.queries: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args, timeout=None):
        self.queries.append((query, args))
        self.timeout = timeout
        if self.fail:
            raise DataProviderUnavailable("down")
        return self.rows


async def test_last_sales_bound_by_customer_and_window() -> None:
    connection = FakeConnection(
        [
            {
                "sku": "12799",
                "fecha": date(2026, 10, 30),
                "nro_transaccion": "FEV-1",
                "unidad_medida_id": "C45",
                "cantidad": Decimal(45),
                "valor_bruto": Decimal(149551),
                "subtotal": Decimal(149551),
                "dscto": Decimal(0),
                "n_lineas": 3,
                "descripcion": "Vaso",
                "unidad_inventario_id": "UNID ",
                "iva_venta_pct": Decimal(19),
                "cod_proveedor": "359",
                "factor_empaque": Decimal(45),
            }
        ]
    )
    source = WarehouseQuotationSource(connection, "consumo")

    sales = await source.last_sales("900315138", 6)

    query, args = connection.queries[0]
    assert '"consumo"."ventas"' in query and '"consumo"."clientes"' in query
    assert 'naturaleza_codigo" = 2' in query and args == ("900315138", 6)
    assert connection.timeout == 30.0
    assert sales[0].net_price.quantize(Decimal("0.01")) == Decimal("3323.36")
    assert sales[0].unit == "UNID" and sales[0].sale_unit == "C45"


async def test_costs_fall_back_to_every_warehouse() -> None:
    connection = FakeConnection(
        [
            {
                "referencia": "2537",
                "costo_co": None,
                "costo_global": Decimal("3500"),
                "fecha_corte": date(2026, 11, 3),
            },
            {
                "referencia": "1485",
                "costo_co": Decimal("2297.85"),
                "costo_global": Decimal("2400"),
                "fecha_corte": date(2026, 11, 3),
            },
        ]
    )
    source = WarehouseQuotationSource(connection)

    costs = await source.costs(["2537", "1485"], "001")

    assert costs["2537"].is_global and costs["2537"].cost == Decimal("3500")
    assert not costs["1485"].is_global and costs["1485"].cost == Decimal("2297.85")
    assert connection.queries[0][1] == (["2537", "1485"], "001")
    assert await source.costs([], "001") == {}


async def test_sellers_match_the_email_lower_case() -> None:
    connection = FakeConnection([{"codigo": "0051 ", "nombre": "PEREZ", "correo": "A@JDH.COM.CO"}])

    sellers = await WarehouseQuotationSource(connection).sellers(" A@jdh.com.co ")

    assert connection.queries[0][1] == ("a@jdh.com.co",)
    assert sellers[0].code == "0051" and sellers[0].email == "a@jdh.com.co"


async def test_outage_is_a_503_and_schema_is_checked() -> None:
    with pytest.raises(DependencyUnavailableError):
        await WarehouseQuotationSource(FakeConnection(fail=True)).branches("1")
    with pytest.raises(ValueError):
        WarehouseQuotationSource(FakeConnection(), 'consumo"; drop')
