"""A month of sales by customer branch and the rep master, read from the warehouse.

Checked against BI's workbook (PLANTILLA PRESUPUESTO, September 2026) on 2026-11-26: the
monthly totals of 2025-09, 2026-06 and 2026-08 match to the peso.

- Sale = ``sum(subtotal)`` of every line except the commercial discounts SKU ``NC530535``,
  compared null-safe: the 2025 rows of C.O. 100 have no SKU and are sales. No filter on
  ``concepto`` or ``estado`` (returns subtract; those old rows are not 'Comprometida').
- One month per query on the ``fecha`` index takes 2–6 s cold and about 1 s warm.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderConnection, DataProviderUnavailable
from app.modules.commercial_budget.domain.entities import CustomerMonth, SalesRep

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SETTINGS = {"jit": "off"}

_MONTH = """
select coalesce(nullif(btrim(v.cliente_key::text), ''), '-1') as cliente_key,
       sum(v.subtotal) as net, sum(v.costo) as cost, count(*) as lines
from {schema}.ventas v
where v.fecha >= $1 and v.fecha < $2
  and btrim(v.sku) is distinct from 'NC530535'
group by 1
"""

_REPS = """
select btrim(v.codigo::text) as codigo, v.nombre, v.estado, v.equipo, v.clase_equipo,
       v.regional, v.unidad_negocio
from {schema}.vendedores v
where coalesce(btrim(v.codigo::text), '') <> ''
"""


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class WarehouseBudgetSource:
    """Sales and reps from ``consumo``."""

    def __init__(
        self, connection: DataProviderConnection, schema: str = "consumo", timeout: float = 300.0
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

    async def _fetch(self, query: str, *args):
        try:
            return await self.connection.fetch(
                query.format(schema=self.schema), *args, timeout=self.timeout, settings=_SETTINGS
            )
        except DataProviderUnavailable as exc:
            raise DependencyUnavailableError(
                "No se pudieron leer las ventas del DWH; intenta de nuevo en un momento"
            ) from exc

    async def month(self, start: date, following: date) -> list[CustomerMonth]:
        """Every customer branch's sales of the month."""

        rows = await self._fetch(_MONTH, start, following)
        return [
            CustomerMonth(
                customer_key=str(row["cliente_key"]),
                net=Decimal(str(row["net"] or 0)),
                cost=Decimal(str(row["cost"] or 0)),
                lines=int(row["lines"] or 0),
            )
            for row in rows
        ]

    async def reps(self) -> list[SalesRep]:
        """The rep master: team, class, regional and business unit of each rep."""

        rows = await self._fetch(_REPS)
        return [
            SalesRep(
                code=row["codigo"],
                name=_text(row["nombre"]),
                team=_text(row["equipo"]),
                team_class=_text(row["clase_equipo"]),
                regional=_text(row["regional"]),
                business_unit=_text(row["unidad_negocio"]),
                is_active=_text(row["estado"]) == "A",
            )
            for row in rows
        ]


__all__ = ["WarehouseBudgetSource"]
