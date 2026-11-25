"""The supplier quotation in force of each reference, from the warehouse: who, where and in
which unit a SIESA load quotes the new cost.

``consumo.cotizaciones_proveedores`` keeps SIESA's quotations (``t212``) as they are each
morning; the one in force in the reference's inventory unit (``productos.unidad_inventario_id``)
is the one the supplier's «MAESTRA» takes its old cost from. 503 references have one in force
from more than one supplier (2026-11-25): the latest activated is taken, and the others are
told.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderConnection, DataProviderUnavailable
from app.modules.commercial_masters.domain.siesa_loads import CurrentQuotation, latest_quotations

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_QUERY = """
select trim(c.sku) as sku, trim(c.proveedor_nit) as nit,
       trim(c.proveedor_sucursal_id) as sucursal, upper(trim(c.unidad_medida_id)) as unidad,
       coalesce(nullif(trim(c.moneda_id), ''), 'COP') as moneda,
       coalesce(c.tiempo_entrega_dias, 0) as dias,
       (c.fecha_activacion at time zone 'America/Bogota')::date as activacion
from {schema}.cotizaciones_proveedores c
join {schema}.productos p on p.sku = c.sku
where c.es_vigente and c.sku = any($1::text[])
  and upper(trim(c.unidad_medida_id)) = upper(trim(p.unidad_inventario_id))
  and c.proveedor_nit is not null
"""


class WarehouseCurrentQuotations:
    """The quotation in force of each reference (``consumo``)."""

    def __init__(
        self, connection: DataProviderConnection, schema: str = "consumo", timeout: float = 30.0
    ) -> None:
        """Create the source.

        Args:
            connection (DataProviderConnection): Read-only warehouse connection.
            schema (str): Consumption schema.
            timeout (float): Seconds for the query.

        Raises:
            ValueError: The schema is not a SQL identifier.
        """

        if not _IDENTIFIER.fullmatch(schema):
            raise ValueError(f"Invalid schema: {schema}")
        self.connection = connection
        self.schema = schema
        self.timeout = timeout

    async def current(self, skus: Sequence[str]) -> dict[str, CurrentQuotation]:
        """The quotation of each reference that has one, by reference.

        Raises:
            DependencyUnavailableError: The warehouse did not answer.
        """

        if not skus:
            return {}
        try:
            rows = await self.connection.fetch(
                _QUERY.format(schema=self.schema), list(skus), timeout=self.timeout
            )
        except DataProviderUnavailable as exc:
            raise DependencyUnavailableError(
                "No se pudieron leer las cotizaciones vigentes del DWH; intenta de nuevo en un "
                "momento"
            ) from exc
        return latest_quotations(
            CurrentQuotation(
                sku=row["sku"],
                nit=row["nit"],
                branch=row["sucursal"] or "001",
                unit=row["unidad"],
                currency=row["moneda"],
                delivery_days=int(row["dias"]),
                activated_on=row["activacion"],
            )
            for row in rows
            if row["sku"] and row["nit"] and row["unidad"]
        )


__all__ = ["WarehouseCurrentQuotations"]
