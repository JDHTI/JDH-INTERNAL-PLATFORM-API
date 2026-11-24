"""Purchase entries and the quotation in force on each one's day, read live from the warehouse.

Views of ``consumo`` (``docs/costos.md``):

- ``entradas_compras``: one row per inventory movement line; only the purchase receipts are
  read (``EA``, «ENTRADA POR COMPRAS», price above 0). ``compra_linea_key`` is SIESA's
  movement rowid: stable between loads.
- ``cotizaciones_proveedores``: every activation of every quotation. The one in force on an
  entry's day is the latest activation on or before it, for the same NIT, reference and unit,
  the same supplier branch first. ``fecha_activacion`` is midnight in Bogotá stored in UTC.
  ``descuento_escala_pct`` (the «Tasa 1») is in points (``20.0000`` is 20 %); the line is
  compared with ``precio_unitario`` less that discount, as SIESA receives it.
- ``productos`` (description, inventory unit) and ``centros_operacion`` (regional).

The line's unit and the two previous prices (``unidad_medida_id``, ``precio_anterior``,
``precio_hace_2``) came with DWH PR #160 (2026-11-23). Until the view has them, the line's unit
is the product's inventory unit and the previous prices are worked out inside the period read
(:attr:`EntryWindow.previous_from_warehouse`).
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderConnection, DataProviderUnavailable
from app.modules.costs.domain.entries import EntryLine, discount_fraction, net_quotation
from app.modules.costs.domain.ports import EntryWindow

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
OPTIONAL_COLUMNS = ("unidad_medida_id", "precio_anterior", "precio_hace_2")
# How long the columns the view has are trusted before asking again (a deploy adds them).
COLUMNS_TTL_SECONDS = 3600.0
_columns_cache: dict[str, tuple[float, frozenset[str]]] = {}

_PURCHASES = (
    "e.documento like 'EA-%' and e.desc_motivo = 'ENTRADA POR COMPRAS' and e.precio_unit_local > 0"
)

_QUERY = """
with e as materialized (
  select e.compra_linea_key, e.fecha, e.co_docto, e.bodega, e.documento, e.docto_orden,
         e.docto_referencia, e.referencia, e.proveedor, e.nit_proveedor_documento,
         e.proveedor_key, e.cantidad, e.precio_unit_local,
         upper(trim({unit})) as unidad,
         {previous} as precio_anterior, {previous_2} as precio_hace_2,
         p.descripcion, co.cod_regional, co.desc_regional
  from {schema}.entradas_compras e
  left join {schema}.productos p on p.producto_key = e.producto_key
  left join {schema}.centros_operacion co on co.centro_operacion_key = e.centro_operacion_key
  where {where} and {purchases}
),
c as materialized (
  select proveedor_key, proveedor_nit, sku, upper(trim(unidad_medida_id)) as unidad,
         (fecha_activacion at time zone 'America/Bogota')::date as desde,
         precio_unitario, descuento_escala_pct
  from {schema}.cotizaciones_proveedores
  where sku in (select distinct referencia from e)
)
select distinct on (e.compra_linea_key)
       e.*, c.desde as cotizacion_desde, c.precio_unitario as cotizacion,
       c.descuento_escala_pct as tasa_1
from e
left join c on c.sku = e.referencia and c.proveedor_nit = e.nit_proveedor_documento
           and c.unidad = e.unidad and c.desde <= e.fecha
order by e.compra_linea_key,
         (c.proveedor_key = e.proveedor_key) desc nulls last,
         c.desde desc nulls last
"""


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _decimal(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def line_from_row(row: Mapping) -> EntryLine:
    """An entry line from a row of the query."""

    discount = discount_fraction(_decimal(row["tasa_1"]))
    list_price = _decimal(row["cotizacion"])
    return EntryLine(
        line_key=row["compra_linea_key"],
        entry_date=row["fecha"],
        co=_text(row["co_docto"]),
        regional_code=_text(row["cod_regional"]),
        regional_name=_text(row["desc_regional"]),
        warehouse=_text(row["bodega"]),
        document=_text(row["documento"]),
        purchase_order=_text(row["docto_orden"]),
        invoice_ref=_text(row["docto_referencia"]),
        sku=_text(row["referencia"]) or "",
        description=_text(row["descripcion"]),
        supplier_label=_text(row["proveedor"]),
        supplier_nit=_text(row["nit_proveedor_documento"]),
        quantity=_decimal(row["cantidad"]),
        unit=_text(row["unidad"]),
        unit_price=_decimal(row["precio_unit_local"]) or Decimal(0),
        quotation=net_quotation(list_price, discount),
        quotation_since=row["cotizacion_desde"],
        discount_pct=discount,
        quotation_list_price=list_price,
        previous_price=_decimal(row["precio_anterior"]),
        antepenultimate_price=_decimal(row["precio_hace_2"]),
    )


class WarehouseEntrySource:
    """Purchase entry lines with their quotation in force, from ``consumo``."""

    def __init__(
        self, connection: DataProviderConnection, schema: str = "consumo", timeout: float = 60.0
    ) -> None:
        """Create the source.

        Args:
            connection (DataProviderConnection): Read-only warehouse connection.
            schema (str): Consumption schema.
            timeout (float): Seconds per query; half a year of entries reads in a few.

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
            return await self.connection.fetch(query, *args, timeout=self.timeout)
        except DataProviderUnavailable as exc:
            raise DependencyUnavailableError(
                "No se pudieron leer las entradas del DWH; intenta de nuevo en un momento"
            ) from exc

    async def columns(self) -> frozenset[str]:
        """The optional columns ``entradas_compras`` has today (kept an hour per process)."""

        cached = _columns_cache.get(self.schema)
        now = time.monotonic()
        if cached is not None and now - cached[0] < COLUMNS_TTL_SECONDS:
            return cached[1]
        rows = await self._fetch(
            "select column_name from information_schema.columns "
            "where table_schema = $1 and table_name = 'entradas_compras' "
            "and column_name = any($2::text[])",
            self.schema,
            list(OPTIONAL_COLUMNS),
        )
        found = frozenset(row["column_name"] for row in rows)
        _columns_cache[self.schema] = (now, found)
        return found

    async def _query(self, where: str) -> tuple[str, bool]:
        found = await self.columns()
        previous = {"precio_anterior", "precio_hace_2"} <= found
        query = _QUERY.format(
            schema=self.schema,
            unit="e.unidad_medida_id" if "unidad_medida_id" in found else "p.unidad_inventario_id",
            previous="e.precio_anterior" if previous else "null::numeric",
            previous_2="e.precio_hace_2" if previous else "null::numeric",
            where=where,
            purchases=_PURCHASES,
        )
        return query, previous

    async def lines(self, since: date, until: date) -> EntryWindow:
        """The purchase entry lines of the days, with their quotation in force.

        Args:
            since (date): First day.
            until (date): Last day.

        Returns:
            EntryWindow: The lines, and whether the previous prices come from the warehouse.

        Raises:
            DependencyUnavailableError: The warehouse did not answer.
        """

        query, previous = await self._query("e.fecha between $1 and $2")
        rows = await self._fetch(query, since, until)
        return EntryWindow(tuple(line_from_row(row) for row in rows), previous)

    async def line(self, line_key: str) -> EntryLine | None:
        """One purchase entry line by its key, or ``None`` (voided, or not a purchase).

        Raises:
            DependencyUnavailableError: The warehouse did not answer.
        """

        query, _ = await self._query("e.compra_linea_key = $1")
        rows = await self._fetch(query, line_key)
        return line_from_row(rows[0]) if rows else None


__all__ = ["OPTIONAL_COLUMNS", "WarehouseEntrySource", "line_from_row"]
