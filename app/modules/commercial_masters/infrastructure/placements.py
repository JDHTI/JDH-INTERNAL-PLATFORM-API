"""Where each reference is set up, read from the data warehouse's inventory.

``consumo.inventario_fecha`` has one row per reference and warehouse with the warehouse's
operation center (``co_bodega``) and SIESA's installation (``instalacion``, where the average
cost is kept); ``consumo.bodegas`` names the installation. A reference counts where it and the
warehouse are active: about 25,000 (reference, center, installation) in 0.2 s (2026-11-24).
"""

from __future__ import annotations

import re
import time

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderConnection, DataProviderUnavailable
from app.modules.commercial_masters.domain.price_increases import Placement

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# The inventory snapshot is taken a few times a day: an hour per process is fresh enough.
TTL_SECONDS = 3600.0
_cache: dict[str, tuple[float, list[Placement]]] = {}

_QUERY = """
select distinct trim(i.referencia) as referencia, trim(i.co_bodega) as co,
       nullif(trim(i.instalacion), '') as instalacion,
       nullif(trim(i.desc_co_bodega), '') as desc_co,
       nullif(trim(b.desc_instalacion), '') as desc_instalacion
from {schema}.inventario_fecha i
left join {schema}.bodegas b on b.bodega_key = i.bodega_key
where i.producto_activo and i.bodega_activa
  and i.referencia is not null and i.co_bodega is not null
"""


class WarehousePlacementSource:
    """The active (reference, operation center, installation) of the inventory."""

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

    async def placements(self) -> list[Placement]:
        """Every active placement, kept an hour per process.

        Raises:
            DependencyUnavailableError: The warehouse did not answer.
        """

        cached = _cache.get(self.schema)
        now = time.monotonic()
        if cached is not None and now - cached[0] < TTL_SECONDS:
            return cached[1]
        try:
            rows = await self.connection.fetch(
                _QUERY.format(schema=self.schema), timeout=self.timeout
            )
        except DataProviderUnavailable as exc:
            raise DependencyUnavailableError(
                "No se pudo leer el inventario del DWH para filtrar por C.O. o instalación; "
                "intenta de nuevo en un momento"
            ) from exc
        placements = [
            Placement(
                sku=row["referencia"],
                co=row["co"],
                installation=row["instalacion"],
                co_name=row["desc_co"],
                installation_name=row["desc_instalacion"],
            )
            for row in rows
            if row["referencia"] and row["co"]
        ]
        _cache[self.schema] = (now, placements)
        return placements


__all__ = ["WarehousePlacementSource"]
