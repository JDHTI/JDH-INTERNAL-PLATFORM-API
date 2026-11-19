"""Warehouse reads of the quoter: bounded, parameterized and mapped."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderUnavailable
from app.modules.quotations.domain import pricing
from app.modules.quotations.infrastructure.dwh_source import WarehouseQuotationSource


class FakeConnection:
    """Answers every query with ``rows``, or each one in turn with ``responses``."""

    def __init__(self, rows=(), fail: bool = False, responses=None, cause=None) -> None:
        self.rows = list(rows)
        self.fail = fail
        self.cause = cause
        self.responses = list(responses) if responses is not None else None
        self.queries: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args, timeout=None, settings=None):
        self.queries.append((query, args))
        self.timeout = timeout
        self.settings = settings
        if self.fail:
            raise DataProviderUnavailable("down") from self.cause
        if self.responses is not None:
            return self.responses.pop(0)
        return self.rows


async def test_last_sales_bound_by_customer_and_window() -> None:
    sale_rows = [
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
            "categoria": "DESECHABLES ",
            "co": "004 ",
        }
    ]
    connection = FakeConnection(
        responses=[[{"cliente_key": "K1"}, {"cliente_key": "K2"}], sale_rows]
    )
    source = WarehouseQuotationSource(connection, "consumo")

    sales = await source.last_sales("900315138", 6)

    keys_query, keys_args = connection.queries[0]
    assert '"consumo"."clientes"' in keys_query and keys_args == (["900315138"],)
    # The sales are bound by the customer's keys, so the planner uses their index.
    query, args = connection.queries[1]
    assert '"consumo"."ventas"' in query and 'v."cliente_key" = ANY($1::text[])' in query
    assert 'naturaleza_codigo" = 2' in query and args == (["K1", "K2"], 6)
    assert connection.timeout == 30.0 and connection.settings == {"jit": "off"}
    assert sales[0].net_price.quantize(Decimal("0.01")) == Decimal("3323.36")
    assert sales[0].unit == "UNID" and sales[0].sale_unit == "C45"
    assert not sales[0].is_active and sales[0].category == "DESECHABLES"
    assert 'p."categoria"' in query and sales[0].center_code == "004"
    # The last sale of each billing center, then the highest net price among them.
    assert 'PARTITION BY v."sku", v."co"' in query
    assert 'PARTITION BY l."sku" ORDER BY l.precio_neto DESC' in query
    assert "l.rn_co = 1" in query and "l.rn = 1" in query


async def test_billing_centers_are_the_active_operation_centers() -> None:
    connection = FakeConnection(
        [
            {"codigo": "001", "descripcion": "ANTIOQUIA"},
            {"codigo": "004 ", "descripcion": "COSTA"},
            {"codigo": " ", "descripcion": "VACIO"},
        ]
    )

    centers = await WarehouseQuotationSource(connection).billing_centers()

    query = connection.queries[0][0]
    assert '"consumo"."centros_operacion"' in query and "\"estado\" = 'A'" in query
    assert [(item.code, item.name) for item in centers] == [
        ("001", "ANTIOQUIA"),
        ("004", "COSTA"),
    ]


async def test_items_carry_the_category() -> None:
    connection = FakeConnection(
        [
            {
                "sku": "26278",
                "descripcion": "Ginebra",
                "unidad_inventario_id": "UNID",
                "iva_venta_pct": Decimal(5),
                "cod_proveedor": "359",
                "factor_empaque": Decimal(12),
                "estado": "A",
                "categoria": "LICORES",
            },
            {
                "sku": "1485",
                "descripcion": "Sin categoría",
                "unidad_inventario_id": "UNID",
                "iva_venta_pct": Decimal(19),
                "cod_proveedor": None,
                "factor_empaque": None,
                "estado": "A",
                "categoria": None,
            },
        ]
    )

    items = await WarehouseQuotationSource(connection).items(["26278", "1485"])

    assert '"categoria"' in connection.queries[0][0]
    assert items["26278"].category == "LICORES" and items["1485"].category is None


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


async def test_seller_contact_by_code() -> None:
    connection = FakeConnection(
        [{"codigo": "0051 ", "nombre": "PEREZ ANA", "correo": "A@JDH.COM.CO", "celular": " 300 "}]
    )

    contact = await WarehouseQuotationSource(connection).seller_contact(" 0051")

    query, args = connection.queries[0]
    assert '"consumo"."vendedores"' in query and '"celular"' in query and args == ("0051",)
    assert (contact.code, contact.email, contact.phone) == ("0051", "a@jdh.com.co", "300")
    assert await WarehouseQuotationSource(FakeConnection()).seller_contact("1") is None


async def test_a_customer_without_branches_reads_no_sales() -> None:
    connection = FakeConnection(responses=[[]])

    assert await WarehouseQuotationSource(connection).last_sales("1", 6) == []
    assert len(connection.queries) == 1


async def test_outage_is_a_503_and_schema_is_checked() -> None:
    with pytest.raises(DependencyUnavailableError, match="No se pudo consultar"):
        await WarehouseQuotationSource(FakeConnection(fail=True)).branches("1")
    # A read that runs out of time says so.
    slow = FakeConnection(fail=True, cause=TimeoutError())
    with pytest.raises(DependencyUnavailableError, match="tardó más de 30 s"):
        await WarehouseQuotationSource(slow).branches("1")
    with pytest.raises(ValueError):
        WarehouseQuotationSource(FakeConnection(), 'consumo"; drop')
