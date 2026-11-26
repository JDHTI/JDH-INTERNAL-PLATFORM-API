"""The warehouse source of the order lines, over a fake connection."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderUnavailable
from app.modules.service_level.infrastructure.dwh_source import WarehouseOrderLineSource

ROW = {
    "linea_key": "abc",
    "pedido_key": "p1",
    "nro_documento": "PV-19029",
    "co": "007",
    "co_nombre": "CARTAGENA ",
    "fecha_documento": date(2026, 11, 20),
    "fecha_hora_actualizacion": datetime(2026, 11, 21, 8, 0),
    "cod_bodega": "CTG1",
    "bodega": "BODEGA CARTAGENA",
    "sku": " 13274 ",
    "descripcion": "PRODUCTO",
    "cod_proveedor": "8",
    "proveedor": "TEAM FOODS",
    "cliente_key": "c1",
    "identificacion": "900100200",
    "razon_social": "CLIENTE S.A.S",
    "id_sucursal": "001",
    "vendedor_key": "v1",
    "nombre_vendedor": "VENDEDOR",
    "cant_pedida": Decimal("12"),
    "cant_remisionada": Decimal("10"),
    "cant_pendiente": Decimal("2"),
    "valor_subtotal": Decimal("120000"),
    "valor_pendiente": Decimal("20000"),
}


class FakeConnection:
    def __init__(self, rows=(), error: Exception | None = None) -> None:
        self.rows = list(rows)
        self.error = error
        self.calls: list[tuple[str, tuple, dict]] = []

    async def fetch(self, query, *args, timeout=None, settings=None):
        self.calls.append((query, args, {"timeout": timeout, "settings": settings}))
        if self.error:
            raise self.error
        return self.rows


async def test_the_lines_of_fulfilled_orders_are_read_with_bound_dates() -> None:
    connection = FakeConnection([ROW])
    source = WarehouseOrderLineSource(connection, timeout=12)

    lines = await source.lines(date(2026, 11, 1), date(2026, 11, 30))

    query, args, options = connection.calls[0]
    assert "consumo.pedidos_venta_lineas" in query and "estado_codigo = 4" in query
    assert args == (date(2026, 11, 1), date(2026, 11, 30))
    assert options == {"timeout": 12, "settings": {"jit": "off"}}
    line = lines[0]
    assert (line.sku, line.co_name, line.customer_nit) == ("13274", "CARTAGENA", "900100200")
    assert line.is_pending and line.value_pending == Decimal(20000)


async def test_the_view_is_detected_and_a_dead_warehouse_is_a_503() -> None:
    assert await WarehouseOrderLineSource(FakeConnection([{"?column?": 1}])).available()
    assert not await WarehouseOrderLineSource(FakeConnection([])).available()
    broken = WarehouseOrderLineSource(FakeConnection(error=DataProviderUnavailable("down")))
    with pytest.raises(DependencyUnavailableError):
        await broken.lines(date(2026, 11, 1), date(2026, 11, 2))


def test_identifiers_are_checked() -> None:
    with pytest.raises(ValueError):
        WarehouseOrderLineSource(FakeConnection(), view="pedidos; drop")
