"""The month's sales and the supplier quotations, read live from the warehouse.

Confirmed with the DWH team (2026-11-24):

- ``consumo.ventas``: one row per sale or return line. ``cantidad`` is in the inventory unit;
  ``costo`` is ``cantidad × f470_costo_prom_uni`` (no VAT); ``subtotal`` is the net sale
  before VAT; returns (``naturaleza_codigo = 1``) come with every value negative, on the
  return's date. ``NC530535`` (commercial discounts) is not a sale. ``estado`` is always
  'Comprometida'.
- A month reads best in one query (``producto_key = ANY`` and the month's dates use their two
  indexes together): seconds warm, minutes cold, so the timeout is long. Avoid the dbt runs
  (04:45, 07:30, 11:50).
- Customers are matched by ``cliente_key``, never by NIT (some NITs repeat).
- ``cotizaciones_proveedores``: every activation of a quotation; the cost is ``costo_bruto``
  per unit (``UNID``) or kilo (``KL``).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderConnection, DataProviderUnavailable
from app.modules.support_settlements.domain.entities import Quote, SaleLine

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SETTINGS = {"jit": "off"}

_SALES = """
select v.venta_linea_key, v.fecha, v.nro_transaccion, v.co, co.descripcion as co_nombre,
       co.desc_regional, v.cliente_key, v.nit_cliente, v.cod_sucursal, btrim(v.sku) as sku,
       v.cantidad, v.kg, v.valor_bruto, v.subtotal, v.costo, v.cod_bodega, v.bodega,
       v.naturaleza_codigo, v.motivo, v.vendedor
from {schema}.ventas v
left join {schema}.centros_operacion co on co.centro_operacion_key = v.centro_operacion_key
where v.producto_key = any($1::text[]) and v.fecha >= $2 and v.fecha < $3
  and btrim(v.sku) is distinct from 'NC530535' and v.cantidad <> 0
"""

_QUOTES = """
select btrim(c.sku) as sku, upper(btrim(c.unidad_medida_id)) as unidad,
       nullif(btrim(c.centro_operacion_id::text), '') as co,
       (c.fecha_activacion at time zone 'America/Bogota')::date as desde,
       cast(c.fecha_hasta as date) as hasta, c.costo_bruto
from {schema}.cotizaciones_proveedores c
where c.producto_key = any($1::text[])
  and upper(btrim(c.unidad_medida_id)) in ('UNID', 'KL')
  and c.costo_bruto > 0
  and (c.fecha_activacion at time zone 'America/Bogota')::date < $3::date
  and (c.fecha_hasta is null or cast(c.fecha_hasta as date) >= $2::date - 400)
"""


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _decimal(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


class WarehouseSettlementSource:
    """Sales lines and quotations from ``consumo``."""

    def __init__(
        self, connection: DataProviderConnection, schema: str = "consumo", timeout: float = 600.0
    ) -> None:
        """Create the source.

        Args:
            connection (DataProviderConnection): Read-only warehouse connection.
            schema (str): Consumption schema.
            timeout (float): Seconds per query: a cold month takes minutes.

        Raises:
            ValueError: The schema is not a SQL identifier.
        """

        if not _IDENTIFIER.fullmatch(schema):
            raise ValueError(f"Invalid schema: {schema}")
        self.connection = connection
        self.schema = schema
        self.timeout = timeout

    async def _fetch(self, query: str, *args):
        try:
            return await self.connection.fetch(
                query.format(schema=self.schema), *args, timeout=self.timeout, settings=_SETTINGS
            )
        except DataProviderUnavailable as exc:
            raise DependencyUnavailableError(
                "No se pudieron leer las ventas del DWH; intenta de nuevo en un momento"
            ) from exc

    async def sales(
        self, start: date, following: date, product_keys: Sequence[str]
    ) -> list[SaleLine]:
        """Every sale and return line of these products in the month."""

        if not product_keys:
            return []
        rows = await self._fetch(_SALES, list(product_keys), start, following)
        return [
            SaleLine(
                line_key=row["venta_linea_key"],
                sale_date=row["fecha"],
                document=_text(row["nro_transaccion"]),
                co=_text(row["co"]),
                co_name=_text(row["co_nombre"]),
                regional=_text(row["desc_regional"]),
                customer_key=_text(row["cliente_key"]),
                nit=_text(row["nit_cliente"]),
                branch_id=_text(row["cod_sucursal"]),
                sku=row["sku"],
                quantity=_decimal(row["cantidad"]) or Decimal(0),
                kg=_decimal(row["kg"]),
                gross=_decimal(row["valor_bruto"]) or Decimal(0),
                net=_decimal(row["subtotal"]) or Decimal(0),
                cost=_decimal(row["costo"]) or Decimal(0),
                warehouse_code=_text(row["cod_bodega"]),
                warehouse_name=_text(row["bodega"]),
                is_return=row["naturaleza_codigo"] == 1,
                reason=_text(row["motivo"]),
                salesperson=_text(row["vendedor"]),
            )
            for row in rows
        ]

    async def quotes(
        self, start: date, following: date, product_keys: Sequence[str]
    ) -> dict[str, list[Quote]]:
        """The quotation activations of these products that may be in force in the month."""

        if not product_keys:
            return {}
        rows = await self._fetch(_QUOTES, list(product_keys), start, following)
        result: dict[str, list[Quote]] = defaultdict(list)
        for row in rows:
            result[row["sku"]].append(
                Quote(
                    sku=row["sku"],
                    since=row["desde"],
                    until=row["hasta"],
                    cost=Decimal(str(row["costo_bruto"])),
                    unit=row["unidad"],
                    co=_text(row["co"]),
                )
            )
        return dict(result)


__all__ = ["WarehouseSettlementSource"]
