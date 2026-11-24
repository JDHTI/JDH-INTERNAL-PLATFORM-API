"""The warehouse query of the entries: columns it adapts to and how rows become lines."""

from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderUnavailable
from app.modules.costs.infrastructure import dwh_source
from app.modules.costs.infrastructure.dwh_source import WarehouseEntrySource


def row(**changes):
    base = {
        "compra_linea_key": "abc",
        "fecha": date(2026, 5, 7),
        "co_docto": "003",
        "bodega": "BTA9 ",
        "documento": "EA-60379",
        "docto_orden": "ODC-59643",
        "docto_referencia": "pmp1375291",
        "referencia": "1001",
        "proveedor": "399 - UNILEVER ANDINA S.A",
        "nit_proveedor_documento": "860002518",
        "proveedor_key": "pk",
        "cantidad": Decimal("812"),
        "precio_unit_local": Decimal("49384.67"),
        "unidad": "UNID",
        "precio_anterior": None,
        "precio_hace_2": None,
        "descripcion": "Salsa de Tomate Fruco Gx4350g",
        "cod_regional": "03",
        "desc_regional": "CENTRO",
        "cotizacion_desde": date(2026, 2, 2),
        "cotizacion": Decimal("49384.67"),
        "tasa_1": Decimal("0"),
    }
    return base | changes


class FakeConnection:
    def __init__(self, columns=(), rows=(), down=False) -> None:
        self.columns = columns
        self.rows = list(rows)
        self.down = down
        self.queries: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args, timeout=None, settings=None):
        self.queries.append((query, args))
        if self.down:
            raise DataProviderUnavailable("down")
        if "information_schema" in query:
            return [{"column_name": name} for name in self.columns]
        return self.rows


@pytest.fixture(autouse=True)
def _fresh_columns(monkeypatch):
    monkeypatch.setattr(dwh_source, "_columns_cache", {})


async def test_without_the_new_columns_it_uses_the_inventory_unit_and_no_previous() -> None:
    connection = FakeConnection(rows=[row()])

    window = await WarehouseEntrySource(connection).lines(date(2026, 5, 1), date(2026, 5, 31))

    query, args = connection.queries[-1]
    assert "p.unidad_inventario_id" in query and "null::numeric as precio_anterior" in query
    assert "e.documento like 'EA-%'" in query and "ENTRADA POR COMPRAS" in query
    assert "at time zone 'America/Bogota'" in query
    assert args == (date(2026, 5, 1), date(2026, 5, 31))
    assert not window.previous_from_warehouse
    entry = window.lines[0]
    assert (entry.warehouse, entry.co, entry.unit_price, entry.quotation) == (
        "BTA9",
        "003",
        Decimal("49384.67"),
        Decimal("49384.67"),
    )
    assert entry.supplier_code == "399" and entry.quotation_since == date(2026, 2, 2)


async def test_with_dwh_pr_160_it_reads_the_line_unit_and_previous_prices() -> None:
    connection = FakeConnection(
        columns=("unidad_medida_id", "precio_anterior", "precio_hace_2"),
        rows=[row(precio_anterior=Decimal("49385"), precio_hace_2=Decimal("49384.67"))],
    )

    window = await WarehouseEntrySource(connection).lines(date(2026, 5, 1), date(2026, 5, 31))

    query, _ = connection.queries[-1]
    assert "upper(trim(e.unidad_medida_id))" in query and "e.precio_anterior" in query
    assert window.previous_from_warehouse
    assert window.lines[0].previous_price == Decimal("49385")
    assert window.lines[0].antepenultimate_price == Decimal("49384.67")


async def test_the_quotation_is_net_of_its_tasa_1_in_points() -> None:
    # UN SOLO PROVEEDOR S.A., EA-205060 (2026-11-23): quoted 8,246 with a Tasa 1 of 20.0000,
    # received at 6,596.80. Another line of the same day came at 8,246: a real novelty.
    rows = [
        row(compra_linea_key="ok", precio_unit_local=Decimal("6596.80")),
        row(compra_linea_key="full", precio_unit_local=Decimal("8246")),
    ]
    connection = FakeConnection(
        rows=[item | {"cotizacion": Decimal("8246"), "tasa_1": Decimal("20.0000")} for item in rows]
    )

    window = await WarehouseEntrySource(connection).lines(date(2026, 11, 1), date(2026, 11, 30))

    ok, full = window.lines
    assert (ok.discount_pct, ok.quotation_list_price, ok.quotation) == (
        Decimal("0.2"),
        Decimal("8246"),
        Decimal("6596.8000"),
    )
    assert ok.difference == 0 and full.difference_pct == Decimal("0.25")
    negative = await WarehouseEntrySource(
        FakeConnection(rows=[row(tasa_1=Decimal("-5"), cotizacion=Decimal("100"))])
    ).lines(date(2026, 11, 1), date(2026, 11, 30))
    assert negative.lines[0].discount_pct == Decimal("0.05")
    assert negative.lines[0].quotation == Decimal("95.0000")


async def test_the_columns_are_asked_once_per_hour() -> None:
    connection = FakeConnection(rows=[])
    source = WarehouseEntrySource(connection)

    await source.lines(date(2026, 5, 1), date(2026, 5, 2))
    await source.line("abc")

    assert sum("information_schema" in query for query, _ in connection.queries) == 1
    assert connection.queries[-1][1] == ("abc",)


async def test_a_missing_line_is_none_and_an_outage_is_a_dependency_error() -> None:
    assert await WarehouseEntrySource(FakeConnection()).line("nope") is None
    with pytest.raises(DependencyUnavailableError):
        await WarehouseEntrySource(FakeConnection(down=True)).lines(
            date(2026, 5, 1), date(2026, 5, 2)
        )


def test_the_schema_must_be_an_identifier() -> None:
    with pytest.raises(ValueError):
        WarehouseEntrySource(FakeConnection(), schema="consumo; drop")
