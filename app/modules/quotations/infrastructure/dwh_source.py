"""Live reads of the data warehouse for the quoter (schema ``consumo``).

Every query names its columns, binds its values (``$n``) and is bounded by customer or by
SKU list. Views: ``ventas`` (one row per invoice line), ``clientes`` (one row per branch),
``productos``, ``producto_impuestos``, ``inventario_fecha`` (SIESA's inventory snapshot of the
morning), ``cotizaciones_proveedores``, ``precios_lista_vigentes``, ``listas_precios`` and
``vendedores``. See ``docs/cotizador-dwh.md``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderConnection, DataProviderUnavailable
from app.modules.quotations.domain import pricing
from app.modules.quotations.domain.entities import (
    BillingCenter,
    Branch,
    CostInfo,
    ItemInfo,
    LastSale,
    ListPrice,
    Seller,
    SellerContact,
    ValueTaxKey,
)
from app.modules.quotations.domain.pricing import StockPlan

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
        """The last price of every SKU sold to the customer (all branches) in the window.

        For each SKU, the last sale of **each** billing operation center (``ventas.co``) is
        taken, and among those the one with the highest net unit price wins: its date,
        document and unit become the last sale. ``sales_in_window`` counts every sale line.
        Only sales (no returns) with quantity and amount above zero. Within a center, ties on
        the date go to the higher document number, then the higher price, then the line key;
        across centers, a tie on the price goes to the later sale. Prices are per inventory
        unit, net of discounts and before VAT.
        """

        rows = await self._fetch(
            "WITH lineas AS ("
            ' SELECT v."sku", v."co", v."fecha", v."nro_transaccion", v."unidad_medida_id",'
            ' v."cantidad", v."valor_bruto", v."subtotal", v."dscto", v."venta_linea_key",'
            ' v."subtotal" / v."cantidad" AS precio_neto,'
            ' count(*) OVER (PARTITION BY v."sku") AS n_lineas,'
            ' row_number() OVER (PARTITION BY v."sku", v."co" ORDER BY v."fecha" DESC,'
            " nullif(regexp_replace(v.\"nro_transaccion\", '\\D', '', 'g'), '')::bigint"
            ' DESC NULLS LAST, v."subtotal" / v."cantidad" DESC, v."venta_linea_key" DESC)'
            ' AS rn_co, v."producto_key"'
            f" FROM {self._view('ventas')} v"
            f' WHERE v."cliente_key" IN (SELECT c."cliente_key" FROM {self._view("clientes")} c'
            ' WHERE c."nit" = $1)'
            ' AND v."naturaleza_codigo" = 2 AND v."cantidad" > 0 AND v."subtotal" > 0'
            ' AND v."fecha" >= (current_date - make_interval(months => $2::int))'
            # The last sale of each center, then the highest price among them.
            "), por_co AS ("
            ' SELECT l.*, row_number() OVER (PARTITION BY l."sku" ORDER BY l.precio_neto DESC,'
            ' l."fecha" DESC, l."venta_linea_key" DESC) AS rn FROM lineas l WHERE l.rn_co = 1'
            # The product is joined to the chosen sale only, not to every line of the window.
            ') SELECT l.*, p."descripcion", p."unidad_inventario_id", p."iva_venta_pct",'
            ' p."cod_proveedor", p."factor_empaque", p."estado", p."categoria" FROM por_co l'
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
                    # Unknown item (no product row): leave it active; the cost tells.
                    is_active=row["estado"] in ("A", None),
                    category=_strip(row["categoria"]),
                    center_code=_strip(row["co"]),
                )
            )
        return sales

    async def billing_centers(self) -> list[BillingCenter]:
        """Active operation centers (``consumo.centros_operacion``, the ``centros-operacion``
        master), one per code, by code."""

        rows = await self._fetch(
            'SELECT DISTINCT ON (trim("codigo")) trim("codigo") AS codigo, "descripcion"'
            f" FROM {self._view('centros_operacion')} WHERE \"estado\" = 'A'"
            ' ORDER BY trim("codigo"), "descripcion"'
        )
        return [
            BillingCenter(str(row["codigo"]).strip(), _strip(row["descripcion"]))
            for row in rows
            if _strip(row["codigo"])
        ]

    async def costs(self, skus: Sequence[str], plan: StockPlan) -> dict[str, CostInfo]:
        """The supplier quotation and the inventory cost of each SKU (skill ``lista-precios``).

        - Quotation: the highest in force per SKU, unit or kilo, item active, gross cost
          rounded, plus ICUI (percent) and IBUA (per unit). Lactalis only from the branch of
          the plan. One more than ``QUOTE_OUTLIER_FACTOR`` times the median last cost of the
          SKU's installations is a capture error: it comes apart as ``discarded_quote``.
        - Inventory: ``inventario_fecha`` (SIESA's own snapshot of the morning), average cost
          of the list's warehouse rounded to pesos; warehouse 2 for frozen and refrigerated
          lines; the highest of the list 1 warehouses when the plan says so. Costs of one peso
          or less are placeholders and never count.
        """

        if not skus:
            return {}
        primary, cold = plan.warehouses or ("", "")
        rows = await self._fetch(
            "WITH prod AS ("
            ' SELECT p."producto_key", p."sku", p."linea", p."estado"'
            f' FROM {self._view("productos")} p WHERE p."sku" = ANY($1::text[])'
            "), ref AS ("
            ' SELECT s."producto_key",'
            ' percentile_cont(0.5) WITHIN GROUP (ORDER BY s."ultimo_costo_unit") AS med'
            ' FROM (SELECT DISTINCT f."producto_key", f."instalacion", f."ultimo_costo_unit"'
            f' FROM {self._view("inventario_fecha")} f JOIN prod USING ("producto_key")'
            ' WHERE f."ultimo_costo_unit" > 1) s GROUP BY 1'
            "), cot AS ("
            ' SELECT c."producto_key", round(round(c."costo_bruto")'
            ' * (1 + coalesce(c."icui_tasa_pct", 0) / 100) + coalesce(c."ibua_valor", 0)) AS costo'
            f' FROM {self._view("cotizaciones_proveedores")} c JOIN prod USING ("producto_key")'
            ' WHERE c."es_vigente" AND c."producto_activo"'
            " AND c.\"unidad_medida_id\" IN ('UNID', 'KL')"
            ' AND (coalesce(c."proveedor_nit", \'\') <> $7 OR c."proveedor_sucursal_id" = $2)'
            "), cot_ok AS ("
            ' SELECT c."producto_key",'
            " max(c.costo) FILTER (WHERE r.med IS NULL OR c.costo <= $8 * r.med) AS costo_cot,"
            " max(c.costo) FILTER (WHERE c.costo > $8 * r.med) AS costo_descartado"
            ' FROM cot c LEFT JOIN ref r USING ("producto_key") GROUP BY 1'
            "), inv AS ("
            ' SELECT f."producto_key",'
            ' max(round(f."costo_prom_unit_ins"))'
            ' FILTER (WHERE round(f."costo_prom_unit_ins") > 1)'
            ' AS costo_inv, sum(f."existencia") AS existencia, max(f."foto_at") AS foto'
            f' FROM {self._view("inventario_fecha")} f JOIN prod pr USING ("producto_key")'
            ' WHERE CASE WHEN $5 THEN f."bodega" = ANY($6::text[])'
            " ELSE f.\"bodega\" = CASE WHEN pr.\"linea\" IN ('CONGELADOS', 'REFRIGERADOS')"
            " THEN $4 ELSE $3 END END"
            " GROUP BY 1"
            ') SELECT pr."sku", pr."linea", pr."estado", c.costo_cot, c.costo_descartado,'
            " i.costo_inv, i.existencia, i.foto"
            ' FROM prod pr LEFT JOIN cot_ok c USING ("producto_key")'
            ' LEFT JOIN inv i USING ("producto_key")',
            list(skus),
            plan.lactalis_branch,
            primary,
            cold,
            plan.highest,
            list(pricing.GENERAL_WAREHOUSES),
            pricing.LACTALIS_NIT,
            pricing.QUOTE_OUTLIER_FACTOR,
        )
        result: dict[str, CostInfo] = {}
        for row in rows:
            sku = str(row["sku"]).strip()
            snapshot = row["foto"]
            result[sku] = CostInfo(
                sku=sku,
                quote_cost=_decimal(row["costo_cot"]),
                discarded_quote=_decimal(row["costo_descartado"]),
                inventory_cost=_decimal(row["costo_inv"]),
                stock=_decimal(row["existencia"]),
                product_line=row["linea"],
                is_active=row["estado"] == "A",
                snapshot_date=snapshot.date() if isinstance(snapshot, datetime) else snapshot,
            )
        return result

    async def price_list_exists(self, price_list_id: str) -> bool:
        """Whether SIESA already has a list with this code (as of yesterday's load)."""

        rows = await self._fetch(
            f'SELECT 1 FROM {self._view("listas_precios")} WHERE "lista_precio_id" = $1 LIMIT 1',
            price_list_id,
        )
        return bool(rows)

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
        """The liquor consumption tax of each SKU, if any (keys ``0011`` imported, ``0024``
        national). Other value taxes (impoconsumo) and VAT are not part of the quotation."""

        if not skus:
            return {}
        rows = await self._fetch(
            'SELECT DISTINCT ON ("sku") "sku", "llave_impuesto_id", "llave_impuesto"'
            f" FROM {self._view('producto_impuestos')}"
            ' WHERE "sku" = ANY($1::text[]) AND "indicador" = \'venta\''
            " AND \"tipo_calculo\" = 'valor' AND \"clase_impuesto\" ILIKE '%licor%'"
            ' ORDER BY "sku", "llave_impuesto_id"',
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

    async def seller_contact(self, code: str) -> SellerContact | None:
        """Name, e-mail (lower case) and mobile of the salesperson ``code``
        (``consumo.vendedores``), whatever their state: the customer still has them assigned."""

        rows = await self._fetch(
            f'SELECT "codigo", "nombre", "correo", "celular" FROM {self._view("vendedores")}'
            ' WHERE trim("codigo") = $1 ORDER BY "estado" = \'A\' DESC LIMIT 1',
            code.strip(),
        )
        if not rows:
            return None
        row = rows[0]
        email = _strip(row["correo"])
        return SellerContact(
            code=str(row["codigo"]).strip(),
            name=_strip(row["nombre"]),
            email=email.lower() if email else None,
            phone=_strip(row["celular"]),
        )

    async def items(self, skus: Sequence[str]) -> dict[str, ItemInfo]:
        """Description, SIESA unit id, VAT, supplier, pack and category of each SKU
        (``productos``)."""

        if not skus:
            return {}
        rows = await self._fetch(
            'SELECT "sku", "descripcion", "unidad_inventario_id", "iva_venta_pct",'
            ' "cod_proveedor", "factor_empaque", "estado", "categoria"'
            f' FROM {self._view("productos")} WHERE "sku" = ANY($1::text[])',
            list(skus),
        )
        return {
            str(row["sku"]).strip(): ItemInfo(
                sku=str(row["sku"]).strip(),
                description=row["descripcion"],
                unit=_strip(row["unidad_inventario_id"]),
                vat_pct=_decimal(row["iva_venta_pct"]),
                supplier_code=_strip(row["cod_proveedor"]),
                pack_factor=_decimal(row["factor_empaque"]),
                is_active=row["estado"] == "A",
                category=_strip(row["categoria"]),
            )
            for row in rows
        }


def _strip(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


__all__ = ["WarehouseQuotationSource"]
