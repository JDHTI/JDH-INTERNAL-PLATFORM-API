"""Live read of the warehouse load sequences (``monitoreo.secuencias``)."""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.infrastructure.data_provider import DataProviderConnection
from app.modules.jobs.domain.entities import SequenceState

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COLUMNS = (
    "secuencia",
    "ultima_corrida_id",
    "ultima_estado",
    "ultima_datos_listos",
    "en_curso",
    "proxima_ejecucion",
    "eta_datos_listos_proxima",
    "eta_datos_listos_en_curso",
)


class WarehouseSequenceSource:
    """One small query per poll, straight to the warehouse (the copy lags minutes)."""

    def __init__(
        self, connection: DataProviderConnection, schema: str = "monitoreo", timeout: float = 15
    ) -> None:
        """Create the source.

        Args:
            connection (DataProviderConnection): Read-only warehouse connection.
            schema (str): Schema of the monitoring views.
            timeout (float): Seconds for the query.

        Raises:
            ValueError: If the schema is not a SQL identifier.
        """

        if not _IDENTIFIER.fullmatch(schema):
            raise ValueError("Invalid monitoring schema")
        self.connection = connection
        self.schema = schema
        self.timeout = timeout

    async def states(self, sequences: Sequence[str]) -> list[SequenceState]:
        """The current state of these sequences (those the warehouse knows)."""

        selected = ", ".join(f'"{column}"' for column in _COLUMNS)
        query = (
            f'SELECT {selected} FROM "{self.schema}"."secuencias" '
            'WHERE "secuencia" = ANY($1::text[]) ORDER BY "secuencia"'
        )
        rows = await self.connection.fetch(query, list(sequences), timeout=self.timeout)
        return [SequenceState(**{column: row[column] for column in _COLUMNS}) for row in rows]
