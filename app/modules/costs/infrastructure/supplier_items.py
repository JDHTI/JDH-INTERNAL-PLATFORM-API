"""The active products of a commercial supplier with their quotation in force, from the
warehouse: what the supplier's «MAESTRA» workbook and the manual form start from.

Checked on 2026-11-23 against the area's ``MAESTRA 50 - ASCENDER S.A.`` workbook: its 115
references match SIESA's supplier SKU, barcode, tax group and old cost (the quotation's unit
price in the inventory unit). The supplier SKU keeps its leading zeros (``01080347``); the
area's Excel lost them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderConnection, DataProviderUnavailable
from app.modules.costs.domain.increases import SupplierItem, as_fraction, with_tax

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_QUERY = """
select distinct on (p.sku)
       p.sku, p.sku_proveedor, p.codigo_barras_principal, p.factor_empaque, p.estado,
       p.categoria, p.grupo_impositivo_id, p.proveedor_etiqueta, p.descripcion,
       i.icui_tasa_pct, c.precio_unitario, c.ibua_valor_um, c.proveedor_nit
from {schema}.productos p
left join {schema}.productos_icui_ibua i on i.producto_key = p.producto_key
left join {schema}.cotizaciones_proveedores c
       on c.producto_key = p.producto_key and c.es_vigente
      and upper(trim(c.unidad_medida_id)) = upper(trim(p.unidad_inventario_id))
where p.estado = 'A' and p.cod_proveedor = $1 {only}
order by p.sku, c.fecha_activacion desc nulls last
"""


def _text(value) -> str | None:
    text = None if value is None else str(value).strip()
    return text or None


def _decimal(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def item_from_row(row: Mapping) -> SupplierItem:
    """A supplier item from a row of the query."""

    icui = as_fraction(_decimal(row["icui_tasa_pct"]))
    ibua = _decimal(row["ibua_valor_um"])
    old_cost = _decimal(row["precio_unitario"])
    return SupplierItem(
        sku=_text(row["sku"]) or "",
        supplier_sku=_text(row["sku_proveedor"]),
        ean=_text(row["codigo_barras_principal"]),
        nit=_text(row["proveedor_nit"]),
        supplier_label=_text(row["proveedor_etiqueta"]),
        description=_text(row["descripcion"]),
        pack_factor=_decimal(row["factor_empaque"]),
        state="Activo",
        category=_text(row["categoria"]),
        tax_group=_text(row["grupo_impositivo_id"]),
        icui_pct=icui,
        ibua=ibua,
        old_cost=old_cost,
        old_cost_with_tax=with_tax(old_cost, icui, ibua),
    )


class WarehouseSupplierItems:
    """Active products of a commercial supplier (``consumo``)."""

    def __init__(
        self, connection: DataProviderConnection, schema: str = "consumo", timeout: float = 30.0
    ) -> None:
        """Create the source.

        Args:
            connection (DataProviderConnection): Read-only warehouse connection.
            schema (str): Consumption schema.
            timeout (float): Seconds per query.

        Raises:
            ValueError: The schema is not a SQL identifier.
        """

        if not _IDENTIFIER.fullmatch(schema):
            raise ValueError(f"Invalid schema: {schema}")
        self.connection = connection
        self.schema = schema
        self.timeout = timeout

    async def items(self, supplier_code: str, skus: list[str] | None = None) -> list[SupplierItem]:
        """The supplier's active products, by reference, with their quotation in force.

        Args:
            supplier_code (str): Commercial supplier code («50»).
            skus (list[str] | None): Only these references.

        Returns:
            list[SupplierItem]: The products.

        Raises:
            DependencyUnavailableError: The warehouse did not answer.
        """

        query = _QUERY.format(
            schema=self.schema, only="and p.sku = any($2::text[])" if skus else ""
        )
        args: tuple = (supplier_code, skus) if skus else (supplier_code,)
        try:
            rows = await self.connection.fetch(query, *args, timeout=self.timeout)
        except DataProviderUnavailable as exc:
            raise DependencyUnavailableError(
                "No se pudieron leer los productos del proveedor en el DWH"
            ) from exc
        return [item_from_row(row) for row in rows]


__all__ = ["WarehouseSupplierItems", "item_from_row"]
