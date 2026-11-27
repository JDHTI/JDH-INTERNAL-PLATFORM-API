"""The products of an order in their warehouses, from the warehouse (``consumo.inventario_fecha``).

``inventario_fecha`` is SIESA's inventory snapshot of the morning, one row per product and
warehouse. ``costo_prom_unit_ins`` is the average unit cost of the warehouse's installation: the
cost SIESA margins an order with. In the files of 2026-11-27 it explains the margin of 635 of 665
lines; the warehouse's own average (``costo_prom_uni_bodega``) explained only 4 more.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderConnection, DataProviderUnavailable
from app.modules.order_validation.domain.entities import InventoryInfo
from app.modules.order_validation.domain.rules import warehouse_key

logger = logging.getLogger(__name__)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class WarehouseInventorySource:
    """Average cost and stock of each product in the warehouse of its order line."""

    def __init__(
        self, connection: DataProviderConnection, schema: str = "consumo", timeout: float = 30.0
    ) -> None:
        """Create the source.

        Raises:
            ValueError: The schema is not a SQL identifier.
        """

        if not _IDENTIFIER.fullmatch(schema):
            raise ValueError("Invalid data provider schema")
        self.connection = connection
        self.schema = schema
        self.timeout = timeout

    async def inventory(
        self, items: Sequence[tuple[str, str]]
    ) -> dict[tuple[str, str], InventoryInfo]:
        """The products in their warehouses, keyed by ``(sku, WAREHOUSE)``.

        A pair SIESA does not have is left out. Bounded by the SKUs and warehouses of the file.

        Raises:
            DependencyUnavailableError: The warehouse could not be read; the file is not
                validated without the costs (the rules on cost and stock would decide wrong).
        """

        wanted = {(sku.strip(), warehouse_key(warehouse)) for sku, warehouse in items}
        wanted = {(sku, warehouse) for sku, warehouse in wanted if sku and warehouse}
        if not wanted:
            return {}
        skus = sorted({sku for sku, _ in wanted})
        warehouses = sorted({warehouse for _, warehouse in wanted})
        try:
            rows = await self.connection.fetch(
                'SELECT "referencia", "bodega", "costo_prom_unit_ins", "existencia", "foto_at"'
                f' FROM "{self.schema}"."inventario_fecha"'
                ' WHERE "referencia" = ANY($1::text[]) AND upper("bodega") = ANY($2::text[])',
                skus,
                warehouses,
                timeout=self.timeout,
                settings={"jit": "off"},
            )
        except DataProviderUnavailable as exc:
            logger.warning("order validation: inventory not read: %r", exc.__cause__ or exc)
            raise DependencyUnavailableError(
                "No se pudo leer el inventario del datawarehouse (costo promedio y existencias por "
                "bodega); inténtalo de nuevo en unos minutos"
            ) from exc
        found: dict[tuple[str, str], InventoryInfo] = {}
        for row in rows:
            key = (str(row["referencia"]).strip(), warehouse_key(row["bodega"]))
            if key not in wanted:
                continue
            snapshot = row["foto_at"]
            found[key] = InventoryInfo(
                sku=key[0],
                warehouse=key[1],
                cost=_decimal(row["costo_prom_unit_ins"]),
                stock=_decimal(row["existencia"]),
                snapshot_date=snapshot.date() if isinstance(snapshot, datetime) else snapshot,
            )
        return found


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


__all__ = ["WarehouseInventorySource"]
