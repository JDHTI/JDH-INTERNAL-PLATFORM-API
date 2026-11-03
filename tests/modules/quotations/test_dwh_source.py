"""Warehouse reads of the quoter: bounded, parameterized and mapped."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderUnavailable
from app.modules.quotations.domain import pricing
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
                "estado": "I",
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
    assert not sales[0].is_active


async def test_costs_compare_quotation_and_inventory_of_the_list_warehouse() -> None:
    snapshot = datetime(2026, 11, 3, 8, 18)
    connection = FakeConnection(
        [
            {
                "sku": "12779",
                "linea": "DESPENSA",
                "estado": "A",
                "costo_cot": None,
                "costo_descartado": Decimal("80552"),
                "costo_inv": Decimal("2049"),
                "existencia": Decimal(0),
                "foto": snapshot,
            },
            {
                "sku": "1081",
                "linea": "REFRIGERADOS",
                "estado": "I",
                "costo_cot": Decimal("13104"),
                "costo_descartado": None,
                "costo_inv": None,
                "existencia": None,
                "foto": None,
            },
        ]
    )
    source = WarehouseQuotationSource(connection)

    costs = await source.costs(["12779", "1081"], pricing.stock_plan("10U", "003"))

    query, args = connection.queries[0]
    assert '"consumo"."inventario_fecha"' in query
    assert '"consumo"."cotizaciones_proveedores"' in query
    assert "es_vigente" in query and "percentile_cont" in query
    assert args[:5] == (["12779", "1081"], "003", "BTA9", "BTA8", False)
    assert args[6:] == ("800245795", Decimal(3))
    assert costs["12779"].discarded_quote == Decimal("80552")
    assert costs["12779"].quote_cost is None and costs["12779"].inventory_cost == Decimal("2049")
    assert costs["12779"].snapshot_date == date(2026, 11, 3)
    assert costs["1081"].quote_cost == Decimal("13104") and not costs["1081"].is_active
    await source.costs(["1"], pricing.stock_plan("1", None))
    # List 1 takes the highest cost among its warehouses.
    assert connection.queries[1][1][4] is True and "BAQ10" in connection.queries[1][1][5]
    assert await source.costs([], pricing.stock_plan("1", None)) == {}


async def test_price_list_exists() -> None:
    assert await WarehouseQuotationSource(FakeConnection([{"?column?": 1}])).price_list_exists(
        "C01"
    )
    assert not await WarehouseQuotationSource(FakeConnection()).price_list_exists("C02")


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
