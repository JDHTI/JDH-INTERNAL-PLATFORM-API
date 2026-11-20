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
import unicodedata
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderConnection, DataProviderUnavailable
from app.modules.quotations.domain import pricing
from app.modules.quotations.domain.entities import (
    BillingCenter,
    Branch,
    CenterWarehouse,
    CostInfo,
    ItemInfo,
    LastSale,
    ListItem,
    ListPrice,
    PriceListInfo,
    Seller,
    SellerContact,
    StockRow,
    ValueTaxKey,
)
from app.modules.quotations.domain.pricing import StockPlan

logger = logging.getLogger(__name__)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Master columns of ``listas_precios`` only: its count columns aggregate every price again.
_LIST_COLUMNS = '"lista_precio_id", "lista_precio", "estado_activo", "clientes_activos_asignados"'


class WarehouseQuotationSource:
    """Customer branches, last sales, current cost, list prices, taxes and sellers."""

    def __init__(
        self,
        connection: DataProviderConnection,
        schema: str = "consumo",
        timeout: float | None = 30.0,
        sales_timeout: float | None = None,
    ) -> None:
        """Create the source.

        Args:
            connection (DataProviderConnection): Read-only warehouse connection.
            schema (str): Consumption schema.
            timeout (float | None): Seconds per query. The sales history of a customer with
                hundreds of branches can pass the pool's default when the warehouse is busy.
            sales_timeout (float | None): Seconds for a customer's or group's sales history
                (building or refreshing a quotation); ``None`` is ``timeout``. On a cold
                cache it passed 30 s, and the retry answered at once (2026-11-19).

        Raises:
            ValueError: The schema is not a SQL identifier.
        """

        if not _IDENTIFIER.fullmatch(schema):
            raise ValueError("Invalid data provider schema")
        self.connection = connection
        self.schema = schema
        self.timeout = timeout
        self.sales_timeout = sales_timeout

    def _view(self, name: str) -> str:
        return f'"{self.schema}"."{name}"'

    async def _fetch(self, query: str, *args: object, timeout: float | None = None):
        limit = timeout if timeout is not None else self.timeout
        try:
            # JIT compiling costs the warehouse about 2 s per read here and saves nothing on
            # reads bounded by customer or SKU list.
            return await self.connection.fetch(query, *args, timeout=limit, settings={"jit": "off"})
        except DataProviderUnavailable as exc:
            cause = exc.__cause__ or exc
            logger.warning("quotations: warehouse query failed: %r", cause)
            if isinstance(cause, TimeoutError):
                raise DependencyUnavailableError(
                    f"El datawarehouse tardó más de {limit or 0:.0f} s en responder; "
                    "inténtalo de nuevo en unos minutos"
                ) from exc
            raise DependencyUnavailableError(
                "No se pudo consultar el datawarehouse; inténtalo de nuevo en unos minutos"
            ) from exc

    async def branches(self, nit: str) -> list[Branch]:
        """Every branch of a customer (``consumo.clientes``)."""

        return await self.group_branches([nit])

    async def group_branches(self, nits: Sequence[str]) -> list[Branch]:
        """Every branch of these NITs, each with its ``nit`` (a business group)."""

        if not nits:
            return []
        rows = await self._fetch(
            'SELECT "nit", "id_sucursal", "razon_social", "cod_canal", "canal", '
            '"lista_precio_id", "lista_precio", "cod_co_factura", "cod_vendedor", '
            f'"nombre_vendedor", "estado", "estado_sucursal" FROM {self._view("clientes")} '
            'WHERE "nit" = ANY($1::text[])',
            list(nits),
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
                nit=_strip(row["nit"]),
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

        The customer's keys are read first and the sales are bound by them: given the NIT
        through a sub-query, the planner walks a whole year of the company's sales by date
        (2.3 million lines) and filters the customer after, which on a cold cache passed the
        30 s limit; by key it reads only the customer's lines through their index.
        """

        return await self.group_last_sales([nit], months)

    async def group_last_sales(self, nits: Sequence[str], months: int) -> list[LastSale]:
        """:meth:`last_sales` over every branch of these NITs (a business group)."""

        if not nits:
            return []
        keys = [
            row["cliente_key"]
            for row in await self._fetch(
                f'SELECT "cliente_key" FROM {self._view("clientes")} WHERE "nit" = ANY($1::text[])',
                list(nits),
            )
        ]
        if not keys:
            return []
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
            ' WHERE v."cliente_key" = ANY($1::text[])'
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
            keys,
            months,
            timeout=self.sales_timeout,
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

    async def center_warehouses(self) -> list[CenterWarehouse]:
        """The active warehouses SIESA classifies for the platform (2026-11-19): main
        (``BP``) and spot requests (``BSP``), plus the promotional ones (``PROMO``,
        2026-11-20), with their operation center.

        Read from the master: ``consumo.bodegas.clasificacion_bodega``, filtered by the code
        (other SIESA groups are also named "BODEGAS PRINCIPALES"), and ``promocional``. If
        SIESA moves a warehouse between groups the list follows it.
        """

        rows = await self._fetch(
            'SELECT trim("codigo") AS codigo, "descripcion", trim("co") AS co,'
            ' CASE WHEN trim("clasificacion_bodega") = ANY($1::text[])'
            ' THEN trim("clasificacion_bodega") ELSE $2 END AS clase'
            f" FROM {self._view('bodegas')}"
            ' WHERE (trim("clasificacion_bodega") = ANY($1::text[]) OR "promocional" = \'Si\')'
            " AND \"estado\" = 'Activo'"
            " ORDER BY 3, 4, 1",
            list(pricing.SITE_CLASSES),
            pricing.PROMO_WAREHOUSES,
        )
        return [
            CenterWarehouse(
                code=str(row["codigo"]),
                name=_strip(row["descripcion"]),
                center_code=str(row["co"]),
                classification=str(row["clase"]),
            )
            for row in rows
            if _strip(row["codigo"]) and _strip(row["co"])
        ]

    async def costs(self, skus: Sequence[str], plan: StockPlan) -> dict[str, CostInfo]:
        """The supplier quotation and the inventory cost of each SKU (skill ``lista-precios``).

        - Quotation: the highest in force per SKU, unit or kilo, item active, gross cost
          rounded, plus ICUI (percent) and IBUA (per unit). Lactalis only from the branch of
          the plan. One more than ``QUOTE_OUTLIER_FACTOR`` times the median last cost of the
          SKU's installations is a capture error: it comes apart as ``discarded_quote``.
        - Inventory: ``inventario_fecha`` (SIESA's own snapshot of the morning), average cost
          of the list's warehouse rounded to pesos; warehouse 2 for frozen and refrigerated
          lines; the highest of the list 1 warehouses when the plan says so, or of the
          warehouses chosen in a center (:meth:`StockPlan.reads`). Costs of one peso or less
          are placeholders and never count.
        - Only warehouses with stock (``existencia > 0``) give the inventory cost
          (2026-11-19). When none of the plan's warehouses has any, their last average cost
          is kept, marked ``inventory_stocked=False``, so the line is not left without cost;
          :func:`pricing.prefer_stocked` drops it when another center has stock.
        """

        if not skus:
            return {}
        dry, cold = plan.reads()
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
            ' AND (coalesce(c."proveedor_nit", \'\') <> $5 OR c."proveedor_sucursal_id" = $2)'
            "), cot_ok AS ("
            ' SELECT c."producto_key",'
            " max(c.costo) FILTER (WHERE r.med IS NULL OR c.costo <= $6 * r.med) AS costo_cot,"
            " max(c.costo) FILTER (WHERE c.costo > $6 * r.med) AS costo_descartado"
            ' FROM cot c LEFT JOIN ref r USING ("producto_key") GROUP BY 1'
            "), inv AS ("
            ' SELECT f."producto_key",'
            ' max(round(f."costo_prom_unit_ins"))'
            ' FILTER (WHERE round(f."costo_prom_unit_ins") > 1 AND f."existencia" > 0)'
            " AS costo_inv_existencia,"
            ' max(round(f."costo_prom_unit_ins"))'
            ' FILTER (WHERE round(f."costo_prom_unit_ins") > 1)'
            ' AS costo_inv, sum(f."existencia") AS existencia,'
            ' sum(f."cant_disponible") AS disponible, max(f."foto_at") AS foto'
            f' FROM {self._view("inventario_fecha")} f JOIN prod pr USING ("producto_key")'
            " WHERE f.\"bodega\" = ANY(CASE WHEN pr.\"linea\" IN ('CONGELADOS', 'REFRIGERADOS')"
            " THEN $4::text[] ELSE $3::text[] END)"
            " GROUP BY 1"
            ') SELECT pr."sku", pr."linea", pr."estado", c.costo_cot, c.costo_descartado,'
            " i.costo_inv_existencia, i.costo_inv, i.existencia, i.disponible, i.foto"
            ' FROM prod pr LEFT JOIN cot_ok c USING ("producto_key")'
            ' LEFT JOIN inv i USING ("producto_key")',
            list(skus),
            plan.lactalis_branch,
            list(dry),
            list(cold),
            pricing.LACTALIS_NIT,
            pricing.QUOTE_OUTLIER_FACTOR,
        )
        result: dict[str, CostInfo] = {}
        for row in rows:
            sku = str(row["sku"]).strip()
            snapshot = row["foto"]
            stocked = _decimal(row.get("costo_inv_existencia"))
            any_cost = _decimal(row["costo_inv"])
            result[sku] = CostInfo(
                sku=sku,
                quote_cost=_decimal(row["costo_cot"]),
                discarded_quote=_decimal(row["costo_descartado"]),
                inventory_cost=stocked if stocked is not None else any_cost,
                inventory_stocked=None if any_cost is None else stocked is not None,
                stock=_decimal(row["existencia"]),
                available=_decimal(row.get("disponible")),
                product_line=row["linea"],
                is_active=row["estado"] == "A",
                snapshot_date=snapshot.date() if isinstance(snapshot, datetime) else snapshot,
            )
        return result

    async def stock(
        self, skus: Sequence[str], warehouses: Sequence[str], centers: Sequence[str] = ()
    ) -> list[StockRow]:
        """Stock of the SKUs in the given warehouses, and in every warehouse of ``centers``
        (operation centers without a list of their own), only where there is some.

        ``cant_disponible`` is what is not committed to orders yet (on 10U, 16 % of the rows
        have part of the stock committed).
        """

        if not skus or not (warehouses or centers):
            return []
        rows = await self._fetch(
            'SELECT p."sku", f."bodega", f."desc_bodega", f."co_bodega", f."existencia",'
            ' f."cant_disponible", f."foto_at"'
            f" FROM {self._view('inventario_fecha')} f"
            f' JOIN {self._view("productos")} p ON p."producto_key" = f."producto_key"'
            ' WHERE p."sku" = ANY($1::text[]) AND f."existencia" > 0 AND f."bodega_activa"'
            ' AND (f."bodega" = ANY($2::text[]) OR trim(f."co_bodega") = ANY($3::text[]))'
            ' ORDER BY p."sku", f."existencia" DESC',
            list(skus),
            list(warehouses),
            list(centers),
        )
        return [
            StockRow(
                sku=str(row["sku"]).strip(),
                warehouse=str(row["bodega"]).strip(),
                warehouse_name=_strip(row["desc_bodega"]),
                center_code=_strip(row["co_bodega"]),
                on_hand=Decimal(row["existencia"]),
                available=_decimal(row["cant_disponible"]),
                snapshot_at=row["foto_at"],
            )
            for row in rows
        ]

    async def price_list_exists(self, price_list_id: str) -> bool:
        """Whether SIESA already has a list with this code (as of yesterday's load)."""

        rows = await self._fetch(
            f'SELECT 1 FROM {self._view("listas_precios")} WHERE "lista_precio_id" = $1 LIMIT 1',
            price_list_id,
        )
        return bool(rows)

    async def price_list_ids(self) -> list[str]:
        """Every list code SIESA has (as of yesterday's load)."""

        rows = await self._fetch(
            f'SELECT DISTINCT "lista_precio_id" FROM {self._view("listas_precios")}'
            ' WHERE "lista_precio_id" IS NOT NULL'
        )
        return [str(row["lista_precio_id"]) for row in rows]

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

    # ------------------------------------------------------------ list updates

    async def price_lists(self, q: str, limit: int = 50) -> list[PriceListInfo]:
        """Lists whose code is ``q`` (any case) or whose name contains it, active first.

        Only the master columns are read: the count columns of ``listas_precios`` (prices in
        force, versions, last change) aggregate the whole ``core.precio_lista`` again per list
        found, which took 24 s for a name search instead of 4 ms. The DWH has no ``unaccent``:
        both sides go upper case without accents.
        """

        term = _plain(q)
        if not term:
            return []
        pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = await self._fetch(
            f"SELECT {_LIST_COLUMNS} FROM {self._view('listas_precios')}"
            ' WHERE upper("lista_precio_id") = $1'
            " OR translate(upper(\"lista_precio\"), 'ÁÉÍÓÚÜÑ', 'AEIOUUN') LIKE $2"
            ' ORDER BY upper("lista_precio_id") = $1 DESC, "estado_activo" DESC,'
            ' "clientes_activos_asignados" DESC NULLS LAST, "lista_precio_id" LIMIT $3',
            term,
            pattern,
            limit,
        )
        return [_price_list(row) for row in rows]

    async def price_list(self, code: str) -> PriceListInfo | None:
        """The list with this code, typed in any case; ``None`` when SIESA has none.

        A code typed by a person is matched upper case on the master and the stored code is
        used from then on (13 real lists have lower-case codes).
        """

        term = (code or "").strip().upper()
        if not term:
            return None
        rows = await self._fetch(
            f"SELECT {_LIST_COLUMNS} FROM {self._view('listas_precios')}"
            ' WHERE upper("lista_precio_id") = $1'
            ' ORDER BY "lista_precio_id" = $1 DESC LIMIT 1',
            term,
        )
        return _price_list(rows[0]) if rows else None

    async def list_branches(self, price_list_id: str) -> list[Branch]:
        """Every branch assigned to a list (``consumo.clientes``), with its NIT and key."""

        rows = await self._fetch(
            'SELECT "nit", "cliente_key", "id_sucursal", "razon_social", "cod_canal", "canal",'
            ' "lista_precio_id", "lista_precio", "cod_co_factura", "cod_vendedor",'
            ' "nombre_vendedor", "estado", "estado_sucursal"'
            f' FROM {self._view("clientes")} WHERE "lista_precio_id" = $1',
            price_list_id,
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
                nit=_strip(row["nit"]),
                customer_key=_strip(row["cliente_key"]),
            )
            for row in rows
        ]

    async def list_items(self, price_list_id: str) -> list[ListItem]:
        """Every SKU with a price in force on the list: one row per SKU, the inventory unit.

        The list is read by its exact code (``trim`` would skip the index: 316 ms instead of
        35). A price of $1 or less is a SIESA placeholder (the promotional list, about 100
        SKUs of list 1): it comes as no price. A unit without a factor (``unid`` lower case)
        has no price per inventory unit either.
        """

        rows = await self._fetch(
            'SELECT DISTINCT ON (l."sku") l."sku", l."producto",'
            ' l."producto_activo", l."categoria", l."iva_venta_pct", l."precio_sin_iva",'
            ' l."precio_unidad_inventario",'
            ' l."impoconsumo_valor" / nullif(l."factor_um", 0) AS impuesto_unidad,'
            ' p."unidad_inventario_id", p."cod_proveedor", p."factor_empaque", p."estado",'
            ' p."descripcion"'
            f" FROM {self._view('precios_lista_vigentes')} l"
            f' LEFT JOIN {self._view("productos")} p ON p."producto_key" = l."producto_key"'
            ' WHERE l."lista_precio_id" = $1'
            ' ORDER BY l."sku", l."es_unidad_inventario" DESC NULLS LAST, l."factor_um"',
            price_list_id,
        )
        result: list[ListItem] = []
        for row in rows:
            sku = str(row["sku"]).strip()
            price = _decimal(row["precio_unidad_inventario"])
            raw = _decimal(row["precio_sin_iva"])
            placeholder = raw is not None and raw <= 1
            state = row["estado"]
            item = ItemInfo(
                sku=sku,
                description=row["descripcion"] or row["producto"],
                unit=_strip(row["unidad_inventario_id"]),
                vat_pct=_decimal(row["iva_venta_pct"]),
                supplier_code=_strip(row["cod_proveedor"]),
                pack_factor=_decimal(row["factor_empaque"]),
                is_active=(state == "A") if state is not None else bool(row["producto_activo"]),
                category=_strip(row["categoria"]),
            )
            result.append(
                ListItem(
                    item=item,
                    price=None if placeholder else price,
                    value_tax_amount=_decimal(row["impuesto_unidad"]),
                    placeholder=placeholder,
                )
            )
        return result

    async def list_last_sales(
        self,
        skus: Sequence[str],
        months: int,
        customer_keys: Sequence[str] | None,
        timeout: float | None = None,
    ) -> dict[str, LastSale]:
        """The last sale of each product: its date, net price and unit cost of goods sold.

        With ``customer_keys`` only the sales to those branches count (the list's customers):
        their lines are read first through the customer index. Without them (a list with
        thousands of branches, where that read passed 20 s) the whole company counts, read in
        two steps: the last month for every product, then the rest of the window for the ones
        with no sale in it (a single 12-month read sorted to disk for 27 s).

        ``ventas.costo`` is the line's total cost and ``cantidad`` is in inventory units, so
        the unit cost is ``costo / cantidad`` (the base of ``precio_unidad_inventario``). A
        cost of zero or less comes as ``None``. ``ventas`` has no index on ``sku`` (it comes
        from a join): the product keys are looked up first and the sales are bound by them.

        Without ``timeout`` the sales reads get ``sales_timeout``, like a quotation's history
        (2026-11-20: a list update used to get only the 30 s of the other reads).
        """

        if not skus:
            return {}
        if timeout is None:
            timeout = self.sales_timeout
        keys = [
            str(row["producto_key"]).strip()
            for row in await self._fetch(
                f'SELECT "producto_key" FROM {self._view("productos")}'
                ' WHERE "sku" = ANY($1::text[])',
                list(skus),
            )
            if row["producto_key"] is not None
        ]
        if not keys:
            return {}
        order = (
            ' ORDER BY v."producto_key", v."fecha" DESC,'
            " nullif(regexp_replace(v.\"nro_transaccion\", '\\D', '', 'g'), '')::bigint"
            ' DESC NULLS LAST, v."venta_linea_key" DESC'
        )
        columns = (
            'v."producto_key", v."sku", v."fecha", v."nro_transaccion", v."co",'
            ' v."unidad_medida_id", v."cantidad", v."costo", v."subtotal", v."valor_bruto",'
            ' v."dscto", v."venta_linea_key"'
        )
        sold = 'v."naturaleza_codigo" = 2 AND v."cantidad" > 0 AND v."subtotal" > 0'
        if customer_keys is not None:
            if not customer_keys:
                return {}
            rows = await self._fetch(
                f"WITH v AS MATERIALIZED (SELECT {columns} FROM {self._view('ventas')} v"
                f' WHERE v."cliente_key" = ANY($1::text[]) AND {sold}'
                # ``+ 0`` keeps the planner off a BitmapAnd with the date index.
                ' AND v."fecha" + 0 >= (current_date - make_interval(months => $2::int)))'
                f' SELECT DISTINCT ON (v."producto_key") v.* FROM v'
                f' WHERE v."producto_key" = ANY($3::text[]){order}',
                list(customer_keys),
                months,
                keys,
                timeout=timeout,
            )
            return _sales(rows)
        recent = await self._fetch(
            f'SELECT DISTINCT ON (v."producto_key") {columns} FROM {self._view("ventas")} v'
            f' WHERE v."producto_key" = ANY($1::text[]) AND {sold}'
            f" AND v.\"fecha\" >= (current_date - interval '1 month'){order}",
            keys,
            timeout=timeout,
        )
        found = _sales(recent)
        seen = {_strip(row["producto_key"]) for row in recent}
        missing = [key for key in keys if key not in seen]
        if missing and months > 1:
            older = await self._fetch(
                f'SELECT DISTINCT ON (v."producto_key") {columns} FROM {self._view("ventas")} v'
                f' WHERE v."producto_key" = ANY($1::text[]) AND {sold}'
                ' AND v."fecha" >= (current_date - make_interval(months => $2::int))'
                f" AND v.\"fecha\" < (current_date - interval '1 month'){order}",
                missing,
                months,
                timeout=timeout,
            )
            found.update(_sales(older))
        return found

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


def _plain(value: str) -> str:
    """Upper case without accents, as the list names are compared."""

    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.upper().split())


def _price_list(row) -> PriceListInfo:
    customers = row["clientes_activos_asignados"]
    return PriceListInfo(
        code=str(row["lista_precio_id"]).strip(),
        name=_strip(row["lista_precio"]),
        is_active=bool(row["estado_activo"]),
        active_branches=int(customers) if customers is not None else None,
    )


def _sales(rows) -> dict[str, LastSale]:
    """The last sale of each SKU from rows of ``ventas`` (one per product)."""

    result: dict[str, LastSale] = {}
    for row in rows:
        quantity = Decimal(row["cantidad"])
        sku = str(row["sku"]).strip()
        cost = _decimal(row["costo"])
        result[sku] = LastSale(
            sku=sku,
            sale_date=row["fecha"],
            document=row["nro_transaccion"],
            sale_unit=_strip(row["unidad_medida_id"]),
            quantity=quantity,
            gross_price=Decimal(row["valor_bruto"]) / quantity,
            net_price=Decimal(row["subtotal"]) / quantity,
            discount_pct=_decimal(row["dscto"]),
            sales_in_window=0,
            center_code=_strip(row["co"]),
            unit_cost=cost / quantity if cost is not None and cost > 0 else None,
        )
    return result


def _strip(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


__all__ = ["WarehouseQuotationSource"]
