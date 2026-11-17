"""Read the ``consumo`` views of the data warehouse for the master sync."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping
from decimal import Decimal
from typing import Any

from app.infrastructure.data_provider import DataProviderConnection
from app.modules.commercial_masters.domain.employee_prices import WarehouseCost

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Bank data of suppliers and contact details are deliberately not selected.
_SUPPLIER_COLUMNS = (
    "proveedor_key",
    "cia",
    "nit",
    "razon_social",
    "id_sucursal",
    "sucursal",
    "nombre_establecimiento",
    "clase_proveedor",
    "tipo_proveedor",
    "ciudad",
    "desc_condicion_pago",
    "estado",
    "estado_sucursal",
    "fecha_ingreso",
)
_CUSTOMER_COLUMNS = (
    "cliente_key",
    "cia",
    "nit",
    "razon_social",
    "id_sucursal",
    "sucursal",
    "nombre_establecimiento",
    "cod_canal",
    "canal",
    "cod_unidad_negocio",
    "unidad_negocio",
    "cod_grupo_empresarial",
    "grupo_empresarial",
    "cod_co_factura",
    "co_factura",
    "zona_logistica",
    "departamento",
    "ciudad",
    "cod_vendedor",
    "nombre_vendedor",
    "estado",
    "estado_sucursal",
)
_PRODUCT_COLUMNS = (
    "producto_key",
    "sku",
    "sku_proveedor",
    "descripcion",
    "estado",
    "cod_proveedor",
    "fabricante",
    "marca",
    "categoria",
    "subcategoria",
    "linea",
    "sublinea",
    "grupo",
    "factor_empaque",
    "unidad_inventario",
    "iva_venta_pct",
    "codigo_barras_principal",
)
_BRIDGE_COLUMNS = (
    "producto_key",
    "proveedor_key",
    "primera_compra",
    "ultima_compra",
    "lineas_compra",
)


class WarehouseMasterSource:
    """Page through ``consumo.*`` with keyset pagination on the surrogate keys."""

    def __init__(self, connection: DataProviderConnection, schema: str = "consumo") -> None:
        """Create the source.

        Args:
            connection (DataProviderConnection): Read-only provider connection.
            schema (str): Schema that holds the consumption views.

        Raises:
            ValueError: If the schema is not a SQL identifier.
        """

        if not _IDENTIFIER.fullmatch(schema):
            raise ValueError("Invalid data provider schema")
        self.connection = connection
        self.schema = schema

    def _relation(self, view: str) -> str:
        return f'"{self.schema}"."{view}"'

    async def _keyset(
        self, view: str, columns: tuple[str, ...], key: str, batch_size: int
    ) -> AsyncIterator[list[Mapping[str, Any]]]:
        selected = ", ".join(f'"{column}"' for column in columns)
        query = (
            f'SELECT {selected} FROM {self._relation(view)} WHERE "{key}" > $1 '
            f'ORDER BY "{key}" ASC LIMIT $2'
        )
        after = ""
        while True:
            rows = await self.connection.fetch(query, after, batch_size)
            if not rows:
                return
            yield [dict(row) for row in rows]
            if len(rows) < batch_size:
                return
            after = rows[-1][key]

    def suppliers(self, batch_size: int) -> AsyncIterator[list[Mapping[str, Any]]]:
        """Pages of ``consumo.proveedores`` ordered by ``proveedor_key``."""

        return self._keyset("proveedores", _SUPPLIER_COLUMNS, "proveedor_key", batch_size)

    def customers(self, batch_size: int) -> AsyncIterator[list[Mapping[str, Any]]]:
        """Pages of ``consumo.clientes`` ordered by ``cliente_key``."""

        return self._keyset("clientes", _CUSTOMER_COLUMNS, "cliente_key", batch_size)

    def products(self, batch_size: int) -> AsyncIterator[list[Mapping[str, Any]]]:
        """Pages of ``consumo.productos`` ordered by ``producto_key``."""

        return self._keyset("productos", _PRODUCT_COLUMNS, "producto_key", batch_size)

    async def product_suppliers(self, batch_size: int) -> AsyncIterator[list[Mapping[str, Any]]]:
        """Pages of the purchase bridge ordered by ``(producto_key, proveedor_key)``."""

        selected = ", ".join(f'"{column}"' for column in _BRIDGE_COLUMNS)
        query = (
            f"SELECT {selected} FROM {self._relation('producto_proveedor')} "
            'WHERE ("producto_key", "proveedor_key") > ($1, $2) '
            'ORDER BY "producto_key" ASC, "proveedor_key" ASC LIMIT $3'
        )
        after_product, after_supplier = "", ""
        while True:
            rows = await self.connection.fetch(query, after_product, after_supplier, batch_size)
            if not rows:
                return
            yield [dict(row) for row in rows]
            if len(rows) < batch_size:
                return
            after_product, after_supplier = rows[-1]["producto_key"], rows[-1]["proveedor_key"]

    async def unit_costs(self) -> list[Mapping[str, Any]]:
        """Current average cost of the minimum unit per SKU, from the inventory photo.

        The cost varies by installation, so it is the stock-weighted average over the
        warehouses that hold stock, or the simple average when the SKU has none. Costs
        are per inventory unit and exclude VAT.
        """

        relation = self._relation("inventario_fecha")
        stocked = 'WHERE "existencia" > 0 AND "costo_prom_unit_ins" > 0'
        query = (
            'SELECT "referencia" AS sku, '
            f'coalesce(sum("existencia" * "costo_prom_unit_ins") FILTER ({stocked}) '
            f'/ nullif(sum("existencia") FILTER ({stocked}), 0), '
            'avg("costo_prom_unit_ins") FILTER (WHERE "costo_prom_unit_ins" > 0)) '
            "AS avg_unit_cost, "
            'max("foto_at")::date AS cut_date '
            f"FROM {relation} "
            'GROUP BY "referencia" '
            'HAVING count(*) FILTER (WHERE "costo_prom_unit_ins" > 0) > 0'
        )
        return [dict(row) for row in await self.connection.fetch(query)]

    async def warehouse_costs(self, warehouse: str) -> list[WarehouseCost]:
        """Average cost per SKU in one warehouse, from the inventory photo.

        Read as the quoter reads a list's warehouse: the cost of each installation rounded to
        pesos, the highest of them, and costs of one peso or less left out (placeholders).
        Costs are per inventory unit and exclude VAT. Each SKU carries the sale VAT rate and
        the tax group of its product (``consumo.productos``), read in the same query.
        """

        query = (
            "WITH inv AS ("
            ' SELECT "producto_key", max(round("costo_prom_unit_ins"))'
            ' FILTER (WHERE round("costo_prom_unit_ins") > 1) AS cost,'
            ' max("foto_at")::date AS cut_date'
            f" FROM {self._relation('inventario_fecha')}"
            ' WHERE "bodega" = $1 GROUP BY 1'
            ') SELECT trim(p."sku") AS sku, max(i.cost) AS cost, max(i.cut_date) AS cut_date,'
            ' max(p."iva_venta_pct") AS vat_pct,'
            ' min(trim(p."grupo_impositivo_id")) AS tax_group_id,'
            ' min(trim(p."grupo_impositivo")) AS tax_group'
            f' FROM inv i JOIN {self._relation("productos")} p USING ("producto_key")'
            " GROUP BY 1"
        )
        return [
            WarehouseCost(
                sku=row["sku"],
                cost=Decimal(row["cost"]),
                snapshot_date=row["cut_date"],
                vat_pct=None if row["vat_pct"] is None else Decimal(row["vat_pct"]),
                tax_group_id=row["tax_group_id"] or None,
                tax_group=row["tax_group"] or None,
            )
            for row in await self.connection.fetch(query, warehouse)
            if row["sku"] and row["cost"] is not None
        ]

    async def commercial_suppliers(self) -> list[Mapping[str, Any]]:
        """Distinct ``(cod_proveedor, proveedor)`` labels with product counts."""

        query = (
            'SELECT "cod_proveedor", max("proveedor") AS proveedor, '
            "count(*) AS productos, "
            "count(*) FILTER (WHERE \"estado\" = 'A') AS productos_activos "
            f"FROM {self._relation('productos')} "
            'WHERE "cod_proveedor" IS NOT NULL GROUP BY "cod_proveedor"'
        )
        return [dict(row) for row in await self.connection.fetch(query)]

    async def commercial_supplier_nits(self, per_supplier: int) -> list[Mapping[str, Any]]:
        """Top NITs per ``cod_proveedor`` by linked products in the purchase history."""

        query = (
            'SELECT "cod_proveedor", "nit", "productos" FROM ('
            'SELECT p."cod_proveedor", pp."nit", count(DISTINCT pp."producto_key") AS productos, '
            'row_number() OVER (PARTITION BY p."cod_proveedor" '
            'ORDER BY count(DISTINCT pp."producto_key") DESC, pp."nit") AS rank '
            f"FROM {self._relation('producto_proveedor')} pp "
            f'JOIN {self._relation("productos")} p ON p."producto_key" = pp."producto_key" '
            'WHERE p."cod_proveedor" IS NOT NULL AND pp."nit" IS NOT NULL '
            'GROUP BY p."cod_proveedor", pp."nit") ranked WHERE rank <= $1'
        )
        return [dict(row) for row in await self.connection.fetch(query, per_supplier)]
