"""Live reads of the data warehouse for the quoter (schema ``consumo``).

Every query names its columns, binds its values (``$n``) and is bounded by customer or by
SKU list. Views: ``ventas`` (one row per invoice line), ``clientes`` (one row per branch),
``productos``, ``producto_impuestos``, ``inventario_corte`` (inventory snapshots, the last one
is today's), ``precios_lista_vigentes``, ``listas_precios`` and ``vendedores``. See
``docs/cotizador-dwh.md``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from decimal import Decimal

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderConnection, DataProviderUnavailable
from app.modules.quotations.domain.entities import (
    Branch,
    CostInfo,
    LastSale,
    ListPrice,
    Seller,
    ValueTaxKey,
)

logger = logging.getLogger(__name__)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class WarehouseQuotationSource:
    """Customer branches, last sales, current cost, list prices, taxes and sellers."""

    def __init__(
        self,
        connection: DataProviderConnection,
        schema: str = "consumo",
        timeout: float | None = 30.0,
    ) -> None:
        """Create the source.

        Args:
            connection (DataProviderConnection): Read-only warehouse connection.
            schema (str): Consumption schema.
            timeout (float | None): Seconds per query. The sales history of a customer with
                hundreds of branches can pass the pool's default when the warehouse is busy.

        Raises:
            ValueError: The schema is not a SQL identifier.
        """

        if not _IDENTIFIER.fullmatch(schema):
            raise ValueError("Invalid data provider schema")
        self.connection = connection
        self.schema = schema
        self.timeout = timeout

    def _view(self, name: str) -> str:
        return f'"{self.schema}"."{name}"'

    async def _fetch(self, query: str, *args: object):
        try:
            return await self.connection.fetch(query, *args, timeout=self.timeout)
        except DataProviderUnavailable as exc:
            logger.warning("quotations: warehouse query failed: %s", exc.__cause__ or exc)
            raise DependencyUnavailableError(
                "No se pudo consultar el datawarehouse; inténtalo de nuevo en unos minutos"
            ) from exc

    async def branches(self, nit: str) -> list[Branch]:
        """Every branch of a customer (``consumo.clientes``)."""

        rows = await self._fetch(
            'SELECT "id_sucursal", "razon_social", "cod_canal", "canal", "lista_precio_id", '
            '"lista_precio", "cod_co_factura", "cod_vendedor", "nombre_vendedor", "estado", '
            f'"estado_sucursal" FROM {self._view("clientes")} WHERE "nit" = $1',
            nit,
        )
        return [
            Branch(
                branch_id=row["id_sucursal"],
                legal_name=row["razon_social"],
                channel_code=row["cod_canal"],
                channel=row["canal"],
                price_list_id=_strip(row["lista_precio_id"]),
                price_list_name=row["lista_precio"],
                billing_center_code=_strip(row["cod_co_factura"]),
                salesperson_code=_strip(row["cod_vendedor"]),
                salesperson_name=row["nombre_vendedor"],
                is_active=row["estado"] == "A" and row["estado_sucursal"] == "A",
            )
            for row in rows
        ]

    async def price_list_customers(self, price_list_id: str) -> int | None:
        """Active customers assigned to a price list (``consumo.listas_precios``)."""

        rows = await self._fetch(
            f'SELECT "clientes_activos_asignados" FROM {self._view("listas_precios")} '
            'WHERE "lista_precio_id" = $1',
            price_list_id,
        )
        if not rows or rows[0]["clientes_activos_asignados"] is None:
            return None
        return int(rows[0]["clientes_activos_asignados"])

    async def last_sales(self, nit: str, months: int) -> list[LastSale]:
        """The last sale of every SKU sold to the customer (all branches) in the window.

        Only sales (no returns) with quantity and amount above zero. Ties on the date go to
        the higher document number, then the higher price, then the line key. Prices are per
        inventory unit, net of discounts and before VAT.
        """

        rows = await self._fetch(
            "WITH lineas AS ("
            ' SELECT v."sku", v."fecha", v."nro_transaccion", v."unidad_medida_id",'
            ' v."cantidad", v."valor_bruto", v."subtotal", v."dscto",'
            ' count(*) OVER (PARTITION BY v."sku") AS n_lineas,'
            ' row_number() OVER (PARTITION BY v."sku" ORDER BY v."fecha" DESC,'
            " nullif(regexp_replace(v.\"nro_transaccion\", '\\D', '', 'g'), '')::bigint"
            ' DESC NULLS LAST, v."subtotal" / v."cantidad" DESC, v."venta_linea_key" DESC) AS rn,'
            ' v."producto_key"'
            f" FROM {self._view('ventas')} v"
            f' WHERE v."cliente_key" IN (SELECT c."cliente_key" FROM {self._view("clientes")} c'
            ' WHERE c."nit" = $1)'
            ' AND v."naturaleza_codigo" = 2 AND v."cantidad" > 0 AND v."subtotal" > 0'
            ' AND v."fecha" >= (current_date - make_interval(months => $2::int))'
            # The product is joined to the last sale only, not to every line of the window.
            ') SELECT l.*, p."descripcion", p."unidad_inventario_id", p."iva_venta_pct",'
            ' p."cod_proveedor", p."factor_empaque" FROM lineas l'
            f' LEFT JOIN {self._view("productos")} p ON p."producto_key" = l."producto_key"'
            ' WHERE l.rn = 1 ORDER BY l."fecha" DESC, l."sku"',
            nit,
            months,
        )
        sales: list[LastSale] = []
        for row in rows:
            quantity = Decimal(row["cantidad"])
            sales.append(
                LastSale(
                    sku=str(row["sku"]).strip(),
                    sale_date=row["fecha"],
                    document=row["nro_transaccion"],
                    sale_unit=_strip(row["unidad_medida_id"]),
                    quantity=quantity,
                    gross_price=Decimal(row["valor_bruto"]) / quantity,
                    net_price=Decimal(row["subtotal"]) / quantity,
                    discount_pct=_decimal(row["dscto"]),
                    sales_in_window=int(row["n_lineas"]),
                    description=row["descripcion"],
                    unit=_strip(row["unidad_inventario_id"]),
                    vat_pct=_decimal(row["iva_venta_pct"]),
                    supplier_code=_strip(row["cod_proveedor"]),
                    pack_factor=_decimal(row["factor_empaque"]),
                )
            )
        return sales

    async def costs(self, skus: Sequence[str], operation_center: str | None) -> dict[str, CostInfo]:
        """Average cost per inventory unit at the last inventory cut.

        The cost of the customer's operation center when it has stock, else the average of
        every warehouse weighted by stock.
        """

        if not skus:
            return {}
        view = self._view("inventario_corte")
        rows = await self._fetch(
            f'WITH ult AS (SELECT max("fecha_corte") AS f FROM {view}),'
            ' stock AS (SELECT "referencia", "co_bodega", "existencia", "costo_prom_unit_ins"'
            f' FROM {view} WHERE "fecha_corte" = (SELECT f FROM ult) AND "existencia" > 0'
            ' AND "referencia" = ANY($1::text[]))'
            ' SELECT "referencia",'
            ' sum("existencia" * "costo_prom_unit_ins") FILTER (WHERE "co_bodega" = $2)'
            ' / nullif(sum("existencia") FILTER (WHERE "co_bodega" = $2), 0) AS costo_co,'
            ' sum("existencia" * "costo_prom_unit_ins") / nullif(sum("existencia"), 0)'
            " AS costo_global,"
            " (SELECT f FROM ult) AS fecha_corte"
            ' FROM stock GROUP BY "referencia"',
            list(skus),
            operation_center or "",
        )
        result: dict[str, CostInfo] = {}
        for row in rows:
            own, overall = _decimal(row["costo_co"]), _decimal(row["costo_global"])
            cost = own if own is not None else overall
            if cost is None:
                continue
            sku = str(row["referencia"]).strip()
            result[sku] = CostInfo(sku, cost, own is None, row["fecha_corte"])
        return result

    async def list_prices(self, price_list_id: str, skus: Sequence[str]) -> dict[str, ListPrice]:
        """Price in force and value tax of each SKU in a list, per inventory unit."""

        if not skus:
            return {}
        rows = await self._fetch(
            'SELECT DISTINCT ON ("sku") "sku", "precio_unidad_inventario",'
            ' "impoconsumo_valor" / nullif("factor_um", 0) AS impuesto_unidad'
            f" FROM {self._view('precios_lista_vigentes')}"
            ' WHERE "lista_precio_id" = $1 AND "sku" = ANY($2::text[])'
            ' ORDER BY "sku", "es_unidad_inventario" DESC NULLS LAST, "factor_um"',
            price_list_id,
            list(skus),
        )
        return {
            str(row["sku"]).strip(): ListPrice(
                sku=str(row["sku"]).strip(),
                price=_decimal(row["precio_unidad_inventario"]),
                value_tax_amount=_decimal(row["impuesto_unidad"]),
            )
            for row in rows
        }

    async def value_tax_keys(self, skus: Sequence[str]) -> dict[str, ValueTaxKey]:
        """The sale tax charged as a value (impoconsumo, liquor) of each SKU, if any."""

        if not skus:
            return {}
        rows = await self._fetch(
            'SELECT DISTINCT ON ("sku") "sku", "llave_impuesto_id", "llave_impuesto"'
            f" FROM {self._view('producto_impuestos')}"
            ' WHERE "sku" = ANY($1::text[]) AND "indicador" = \'venta\''
            ' AND "tipo_calculo" = \'valor\' ORDER BY "sku", "llave_impuesto_id"',
            list(skus),
        )
        return {
            str(row["sku"]).strip(): ValueTaxKey(
                str(row["sku"]).strip(),
                str(row["llave_impuesto_id"]).strip(),
                row["llave_impuesto"],
            )
            for row in rows
        }

    async def sellers(self, email: str) -> list[Seller]:
        """Active salespeople whose corporate e-mail is ``email`` (stored upper case)."""

        rows = await self._fetch(
            f'SELECT "codigo", "nombre", "correo" FROM {self._view("vendedores")}'
            ' WHERE lower("correo") = $1 AND "estado" = \'A\' ORDER BY "codigo"',
            email.strip().lower(),
        )
        return [
            Seller(str(row["codigo"]).strip(), row["nombre"], (row["correo"] or "").lower())
            for row in rows
        ]


def _strip(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


__all__ = ["WarehouseQuotationSource"]
