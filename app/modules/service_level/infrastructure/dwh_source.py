"""The lines of the fulfilled sales orders, read from the warehouse.

``consumo.pedidos_venta`` holds one row per order (SIESA t430) and no lines. The lines (t431)
are published by the DWH as ``consumo.pedidos_venta_lineas`` (DWH PR #164, 2026-11-26), one row
per order line of any state since 2024, built by the ``cartera_pedidos`` sequence (Mon-Sat 11:50,
re-reading 45 days) over ``core.pedido_venta_linea`` (indexed by order date and state):

- ``linea_key`` (the SIESA line), ``pedido_key``, ``nro_documento``, ``estado_codigo``
  (4 «Cumplido»), ``fecha_documento`` (the order date), ``fecha_hora_actualizacion``.
- ``co`` / ``desc_co``, ``cod_bodega`` / ``bodega``.
- A fulfilled order keeps the lines it closed without dispatching (line state 0 to 3, 2.8 % of
  November 2026): they are the unserved part, so they count.
- ``sku``, ``descripcion``, ``cod_proveedor`` / ``proveedor`` (the commercial supplier).
- ``cliente_key``, ``identificacion`` (the NIT the ERP shows), ``razon_social``,
  ``id_sucursal``, ``vendedor_key``, ``nombre_vendedor`` (the order's seller).
- ``cant_pedida``, ``cant_remisionada``, ``cant_pendiente`` (base unit), ``valor_subtotal``
  (gross less discounts, without taxes) and ``valor_pendiente``.

Until the view exists :meth:`WarehouseOrderLineSource.available` says so and the job waits.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderConnection, DataProviderUnavailable
from app.modules.service_level.domain.entities import ZERO, OrderLine

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FULFILLED = 4

_QUERY = """
select l.linea_key, l.pedido_key, l.nro_documento, l.co, l.desc_co as co_nombre,
       l.fecha_documento, l.fecha_hora_actualizacion, l.cod_bodega, l.bodega, l.sku,
       l.descripcion, l.cod_proveedor, l.proveedor, l.cliente_key, l.identificacion,
       l.razon_social, l.id_sucursal, l.vendedor_key, l.nombre_vendedor, l.cant_pedida,
       l.cant_remisionada, l.cant_pendiente, l.valor_subtotal, l.valor_pendiente
from {schema}.{view} l
where l.estado_codigo = {fulfilled} and l.fecha_documento between $1 and $2
"""


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _decimal(value) -> Decimal:
    return ZERO if value is None else Decimal(str(value))


def line_from_row(row: Mapping) -> OrderLine:
    """An order line from a row of the query."""

    ordered = _decimal(row["cant_pedida"])
    pending = max(_decimal(row["cant_pendiente"]), ZERO)
    updated = row["fecha_hora_actualizacion"]
    return OrderLine(
        line_key=str(row["linea_key"]),
        order_key=str(row["pedido_key"]),
        document=_text(row["nro_documento"]),
        co=_text(row["co"]),
        co_name=_text(row["co_nombre"]),
        order_date=row["fecha_documento"],
        updated_at=updated if isinstance(updated, datetime) else None,
        warehouse_code=_text(row["cod_bodega"]),
        warehouse_name=_text(row["bodega"]),
        sku=_text(row["sku"]) or "",
        description=_text(row["descripcion"]),
        supplier_code=_text(row["cod_proveedor"]),
        supplier_name=_text(row["proveedor"]),
        customer_key=_text(row["cliente_key"]),
        customer_nit=_text(row["identificacion"]),
        customer_name=_text(row["razon_social"]),
        customer_branch=_text(row["id_sucursal"]),
        seller_key=_text(row["vendedor_key"]),
        seller_name=_text(row["nombre_vendedor"]),
        qty_ordered=ordered,
        qty_dispatched=_decimal(row["cant_remisionada"]),
        qty_pending=pending,
        value_ordered=_decimal(row["valor_subtotal"]),
        value_pending=max(_decimal(row["valor_pendiente"]), ZERO),
    )


class WarehouseOrderLineSource:
    """The lines of the fulfilled sales orders, from ``consumo``."""

    def __init__(
        self,
        connection: DataProviderConnection,
        schema: str = "consumo",
        view: str = "pedidos_venta_lineas",
        timeout: float = 300.0,
    ) -> None:
        """Create the source.

        Args:
            connection (DataProviderConnection): Read-only warehouse connection.
            schema (str): Consumption schema.
            view (str): The order lines view.
            timeout (float): Seconds per query (a month of lines is about 150,000 rows).

        Raises:
            ValueError: The schema or the view is not a SQL identifier.
        """

        for name in (schema, view):
            if not _IDENTIFIER.fullmatch(name):
                raise ValueError(f"Invalid identifier: {name}")
        self.connection = connection
        self.schema = schema
        self.view = view
        self.timeout = timeout

    async def _fetch(self, query: str, *args):
        try:
            return await self.connection.fetch(
                query, *args, timeout=self.timeout, settings={"jit": "off"}
            )
        except DataProviderUnavailable as exc:
            raise DependencyUnavailableError(
                "No se pudieron leer los pedidos del DWH; intenta de nuevo en un momento"
            ) from exc

    async def available(self) -> bool:
        """Whether the warehouse publishes the order lines view yet."""

        rows = await self._fetch(
            "select 1 from information_schema.tables where table_schema = $1 and table_name = $2",
            self.schema,
            self.view,
        )
        return bool(rows)

    async def lines(self, since: date, until: date) -> list[OrderLine]:
        """Every line of the orders fulfilled with an order date in the days.

        Raises:
            DependencyUnavailableError: The warehouse did not answer.
        """

        query = _QUERY.format(schema=self.schema, view=self.view, fulfilled=FULFILLED)
        rows = await self._fetch(query, since, until)
        return [line_from_row(row) for row in rows]


__all__ = ["FULFILLED", "WarehouseOrderLineSource", "line_from_row"]
