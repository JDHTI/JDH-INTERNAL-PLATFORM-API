"""Read the ``monitoreo`` views of the data warehouse."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Any

from app.infrastructure.data_provider import DataProviderConnection
from app.modules.dwh_monitoring.domain.ports import Row
from app.modules.dwh_monitoring.domain.views import INCREMENTAL_TIME_COLUMN, ViewSpec

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class WarehouseMonitoringSource:
    """Select the contract columns of each view, never ``*``.

    Naming the columns makes a column the contract dropped fail loudly, and a column
    the warehouse added later is simply not read.
    """

    def __init__(self, connection: DataProviderConnection, schema: str = "monitoreo") -> None:
        """Create the source.

        Args:
            connection (DataProviderConnection): Read-only provider connection.
            schema (str): Schema that holds the monitoring views.

        Raises:
            ValueError: If the schema is not a SQL identifier.
        """

        if not _IDENTIFIER.fullmatch(schema):
            raise ValueError("Invalid monitoring schema")
        self.connection = connection
        self.schema = schema

    def _select(self, view: ViewSpec) -> str:
        columns = ", ".join(f'"{column}"' for column in view.source_columns)
        return f'SELECT {columns} FROM "{self.schema}"."{view.name}"'

    async def check(self) -> None:
        """Open the pool and run a trivial read-only query."""

        await self.connection.check()

    async def read_all(self, view: ViewSpec) -> list[Row]:
        """Every row of a small view.

        Args:
            view (ViewSpec): The view.

        Returns:
            list[Row]: Its rows.
        """

        return [dict(row) for row in await self.connection.fetch(self._select(view))]

    async def read_since(
        self, view: ViewSpec, since: datetime, batch_size: int
    ) -> AsyncIterator[list[Row]]:
        """Pages ordered by ``(inicio, key)`` with keyset pagination.

        Args:
            view (ViewSpec): An incremental view keyed by one column.
            since (datetime): Lower bound for ``inicio`` (inclusive).
            batch_size (int): Rows per page.

        Yields:
            list[Row]: The next page.
        """

        (key,) = view.key
        time = INCREMENTAL_TIME_COLUMN
        base = self._select(view)
        first = f'{base} WHERE "{time}" >= $1 ORDER BY "{time}", "{key}" LIMIT $2'
        after = (
            f'{base} WHERE "{time}" >= $1 AND ("{time}", "{key}") > ($2, $3) '
            f'ORDER BY "{time}", "{key}" LIMIT $4'
        )
        rows = await self.connection.fetch(first, since, batch_size)
        while rows:
            yield [dict(row) for row in rows]
            if len(rows) < batch_size:
                return
            last = rows[-1]
            rows = await self.connection.fetch(after, since, last[time], last[key], batch_size)

    async def read_keys(self, view: ViewSpec, keys: Sequence[Any]) -> list[Row]:
        """Rows of an incremental view by key.

        Args:
            view (ViewSpec): An incremental view keyed by one ``uuid`` column.
            keys (Sequence[Any]): Keys to read.

        Returns:
            list[Row]: The rows found.
        """

        if not keys:
            return []
        (key,) = view.key
        query = f'{self._select(view)} WHERE "{key}" = ANY($1::uuid[])'
        return [dict(row) for row in await self.connection.fetch(query, list(keys))]
