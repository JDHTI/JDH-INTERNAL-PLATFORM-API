"""Warehouse reads of the list updates' detail and of the liquor tax screen.

Validated against the DWH on 2026-11-12 (``docs/cotizador-dwh.md`` §10):

- ``inventario_fecha`` is SIESA's full stock photo of the morning (about 08:15 Bogotá);
  the warehouses are filtered by a list of each list's own (``bodegas.facturable`` cannot
  tell the obsolete or transit ones apart).
- ``precios_lista_historico`` keeps a row per price version; ``vigente_hasta`` is the next
  version's start, and an inactivation date may end it before. The main lists only keep
  versions from 2026: older ones are not in SIESA any more.
- The liquors are tax class ``99`` (indexed); their value per unit is on the inventory-unit
  row of ``precios_lista_vigentes``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

from app.modules.quotations.domain.entities import MonthlySale, PriceVersion
from app.modules.quotations.domain.liquor import LiquorListPrice, LiquorProduct, LiquorSuspect
from app.modules.quotations.infrastructure.dwh_source import (
    WarehouseQuotationSource,
    _decimal,
    _strip,
)

# Tax class of the liquor consumption tax (keys 0011 imported and 0024 national).
LIQUOR_CLASS = 99
# Tax groups of liquors and wines that do not carry the key: 0507 "IVA 5% licores".
LIQUOR_GROUPS = ("0503", "0507", "0509")
# Each monthly sales read of one product takes well under a second.
SALES_TIMEOUT = 10.0


def _amount(value: object) -> Decimal | None:
    """A value per unit to 4 decimals (a division by the unit factor brings 16)."""

    number = _decimal(value)
    return None if number is None else number.quantize(_STEP)


_STEP = Decimal("0.0001")


def _day(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value  # type: ignore[return-value]


class WarehouseInsightSource(WarehouseQuotationSource):
    """List price history, monthly sales and liquor taxes (the stock by warehouse is the base
    source's :meth:`WarehouseQuotationSource.stock`)."""

    async def price_history(self, price_list_id: str, sku: str, months: int) -> list[PriceVersion]:
        """Versions of the SKU's price on the list that were in force in the last ``months``.

        Placeholder prices ($1 or less) are left out: they are gaps, not prices. Consecutive
        versions with the same price (a list loaded again) come as one.
        """

        rows = await self._fetch(
            'SELECT h."vigente_desde", least(h."vigente_hasta", h."fecha_inactivacion") AS hasta,'
            ' h."precio_unidad_inventario",'
            ' h."impoconsumo_valor" / nullif(h."factor_um", 0) AS impuesto'
            f" FROM {self._view('precios_lista_historico')} h"
            ' WHERE h."lista_precio_id" = $1 AND h."producto_key" = ANY(ARRAY('
            f'SELECT "producto_key" FROM {self._view("productos")} WHERE "sku" = $2))'
            ' AND h."es_unidad_inventario" AND h."precio_sin_iva" > 1'
            ' AND h."vigente_desde" <= now()'
            ' AND coalesce(least(h."vigente_hasta", h."fecha_inactivacion"), \'infinity\')'
            " > now() - make_interval(months => $3::int)"
            ' ORDER BY h."vigente_desde"',
            price_list_id,
            sku,
            months,
        )
        versions: list[PriceVersion] = []
        for row in rows:
            price = _decimal(row["precio_unidad_inventario"])
            start = _day(row["vigente_desde"])
            if price is None or start is None:
                continue
            end = _day(row["hasta"])
            if versions and versions[-1].price == price and versions[-1].valid_to == start:
                versions[-1] = PriceVersion(
                    versions[-1].valid_from, end, price, versions[-1].value_tax_amount
                )
                continue
            versions.append(PriceVersion(start, end, price, _amount(row["impuesto"])))
        return versions

    async def monthly_sales(
        self, sku: str, months: int, customer_keys: Sequence[str] | None
    ) -> list[MonthlySale]:
        """Net price per inventory unit and quantity of each month's sales of the SKU, to the
        given branches or, without them, to the whole company."""

        keys = [
            str(row["producto_key"]).strip()
            for row in await self._fetch(
                f'SELECT "producto_key" FROM {self._view("productos")} WHERE "sku" = $1', sku
            )
            if row["producto_key"] is not None
        ]
        if not keys or (customer_keys is not None and not customer_keys):
            return []
        sold = 'v."naturaleza_codigo" = 2 AND v."cantidad" > 0 AND v."subtotal" > 0'
        window = (
            "v.\"fecha\" >= (date_trunc('month', current_date) - make_interval(months => $2::int))"
        )
        if customer_keys is None:
            # Month by month: each read goes through the date index (0.6 s for 12 months of a
            # cold SKU on 2026-11-12); one 12-month read walks every line of the product
            # (7.5 s cold).
            rows = []
            for offset in range(months - 1, -1, -1):
                rows += await self._fetch(
                    "SELECT (date_trunc('month', current_date)"
                    " - make_interval(months => $2::int))::date AS mes,"
                    ' sum(v."subtotal") / sum(v."cantidad") AS precio,'
                    ' sum(v."cantidad") AS cantidad'
                    f" FROM {self._view('ventas')} v"
                    f' WHERE v."producto_key" = ANY($1::text[]) AND {sold} AND {window}'
                    " AND v.\"fecha\" < date_trunc('month', current_date)"
                    " - make_interval(months => $2::int - 1)",
                    keys,
                    offset,
                    timeout=SALES_TIMEOUT,
                )
        else:
            rows = await self._fetch(
                "SELECT date_trunc('month', v.\"fecha\")::date AS mes,"
                ' sum(v."subtotal") / sum(v."cantidad") AS precio, sum(v."cantidad") AS cantidad'
                f" FROM {self._view('ventas')} v"
                f' WHERE v."producto_key" = ANY($1::text[]) AND {sold} AND {window}'
                ' AND v."cliente_key" = ANY($3::text[])'
                " GROUP BY 1 ORDER BY 1",
                keys,
                months - 1,
                list(customer_keys),
                timeout=SALES_TIMEOUT,
            )
        return [
            MonthlySale(
                month=_day(row["mes"]),  # type: ignore[arg-type]
                price=Decimal(row["precio"]),
                quantity=Decimal(row["cantidad"]),
            )
            for row in rows
            if row["precio"] is not None
        ]

    # ------------------------------------------------------------ liquor tax

    async def liquor_products(self, skus: Sequence[str] | None = None) -> list[LiquorProduct]:
        """Active products with a liquor tax key and their price on every list in force.

        25 ms on the warehouse for the 2,050 prices of the 17 liquors of 2026-11-12.
        """

        rows = await self._fetch(
            "WITH k AS ("
            ' SELECT p."producto_key", p."sku", p."descripcion", p."grupo_impositivo_id",'
            ' i."llave_impuesto_id", i."llave_impuesto"'
            f" FROM {self._view('productos')} p"
            f' JOIN {self._view("producto_impuestos")} i ON i."producto_key" = p."producto_key"'
            " WHERE p.\"estado\" = 'A' AND i.\"indicador\" = 'venta'"
            ' AND i."tipo_calculo" = \'valor\' AND i."clase_impuesto_id" = $1'
            ' AND ($2::text[] IS NULL OR p."sku" = ANY($2::text[])))'
            ' SELECT k."sku", k."descripcion", k."grupo_impositivo_id", k."llave_impuesto_id",'
            ' k."llave_impuesto", v."lista_precio_id", v."lista_activa", v."unidad_medida_id",'
            ' v."precio_sin_iva", v."es_unidad_inventario", v."vigente_desde",'
            ' v."impoconsumo_valor" / nullif(v."factor_um", 0) AS impuesto_unidad'
            f" FROM k LEFT JOIN {self._view('precios_lista_vigentes')} v"
            ' ON v."producto_key" = k."producto_key" AND v."es_unidad_inventario"'
            ' ORDER BY k."sku", v."lista_precio_id"',
            LIQUOR_CLASS,
            list(skus) if skus is not None else None,
        )
        products: dict[str, dict[str, object]] = {}
        for row in rows:
            sku = str(row["sku"]).strip()
            entry = products.setdefault(
                sku,
                {
                    "description": row["descripcion"],
                    "tax_key": str(row["llave_impuesto_id"]).strip(),
                    "tax_key_name": _strip(row["llave_impuesto"]),
                    "tax_group": _strip(row["grupo_impositivo_id"]),
                    "prices": [],
                },
            )
            if row["lista_precio_id"] is None:
                continue
            entry["prices"].append(  # type: ignore[union-attr]
                LiquorListPrice(
                    price_list_id=str(row["lista_precio_id"]).strip(),
                    list_active=bool(row["lista_activa"]),
                    unit=_strip(row["unidad_medida_id"]),
                    price=_decimal(row["precio_sin_iva"]),
                    tax_amount=_amount(row["impuesto_unidad"]),
                    valid_from=_day(row["vigente_desde"]),
                )
            )
        return [
            LiquorProduct(
                sku=sku,
                description=entry["description"],  # type: ignore[arg-type]
                tax_key=entry["tax_key"],  # type: ignore[arg-type]
                tax_key_name=entry["tax_key_name"],  # type: ignore[arg-type]
                tax_group=entry["tax_group"],  # type: ignore[arg-type]
                prices=tuple(entry["prices"]),  # type: ignore[arg-type]
            )
            for sku, entry in products.items()
        ]

    async def liquor_suspects(self, excluded_lists: Sequence[str] = ()) -> list[LiquorSuspect]:
        """Active products of a liquor tax group without the liquor key (12 in group 0507
        on 2026-11-12), with the lists that still carry a value for them, not counting
        ``excluded_lists`` (upper case)."""

        rows = await self._fetch(
            'SELECT p."sku", p."descripcion", p."grupo_impositivo_id", p."grupo_impositivo",'
            " (SELECT count(*) FROM"
            f' {self._view("precios_lista_vigentes")} v WHERE v."producto_key" = p."producto_key"'
            ' AND v."es_unidad_inventario" AND v."impoconsumo_valor" > 0'
            ' AND NOT upper(trim(v."lista_precio_id")) = ANY($3::text[])) AS con_valor'
            f" FROM {self._view('productos')} p"
            ' WHERE p."estado" = \'A\' AND p."grupo_impositivo_id" = ANY($1::text[])'
            " AND NOT EXISTS (SELECT 1 FROM"
            f' {self._view("producto_impuestos")} i WHERE i."producto_key" = p."producto_key"'
            ' AND i."indicador" = \'venta\' AND i."clase_impuesto_id" = $2)'
            ' ORDER BY p."sku"',
            list(LIQUOR_GROUPS),
            LIQUOR_CLASS,
            [code.strip().upper() for code in excluded_lists if code.strip()],
        )
        return [
            LiquorSuspect(
                sku=str(row["sku"]).strip(),
                description=row["descripcion"],
                tax_group=_strip(row["grupo_impositivo_id"]),
                tax_group_name=_strip(row["grupo_impositivo"]),
                lists_with_value=int(row["con_valor"] or 0),
            )
            for row in rows
        ]


__all__ = ["WarehouseInsightSource"]
