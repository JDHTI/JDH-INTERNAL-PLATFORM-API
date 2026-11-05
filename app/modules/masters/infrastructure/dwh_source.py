"""Read any declared master from the data warehouse, in pages by its key."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any

import asyncpg

from app.infrastructure.data_provider import DataProviderConnection, DataProviderUnavailable
from app.modules.masters.domain.entities import MasterSpec
from app.modules.masters.domain.ports import Row

logger = logging.getLogger(__name__)


def _missing_relation(exc: DataProviderUnavailable) -> bool:
    return isinstance(exc.__cause__, asyncpg.UndefinedTableError)


class WarehouseCatalogSource:
    """Keyset pages of ``<schema>.<relation>`` ordered by the master's key column.

    dbt drops and re-creates the ``consumo`` views while a sequence runs, so a missing
    relation is retried a few times before it fails the master.
    """

    def __init__(
        self,
        connection: DataProviderConnection,
        timeout: float = 120.0,
        retries: int = 3,
        retry_wait: float = 20.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Create the source.

        Args:
            connection (DataProviderConnection): Read-only warehouse connection.
            timeout (float): Seconds per query (a full page of a large view).
            retries (int): Extra attempts when the relation does not exist.
            retry_wait (float): Seconds between those attempts.
            sleep (Callable[[float], Awaitable[None]]): Waits, for tests.
        """

        self.connection = connection
        self.timeout = timeout
        self.retries = retries
        self.retry_wait = retry_wait
        self.sleep = sleep

    async def _fetch(self, query: str, *args: Any) -> Sequence[Any]:
        attempt = 0
        while True:
            try:
                return await self.connection.fetch(query, *args, timeout=self.timeout)
            except DataProviderUnavailable as exc:
                if not _missing_relation(exc) or attempt >= self.retries:
                    raise
                attempt += 1
                logger.warning(
                    "Master sync: relation missing, retry %s: %s", attempt, exc.__cause__
                )
                await self.sleep(self.retry_wait)

    async def records(self, spec: MasterSpec, batch_size: int) -> AsyncIterator[list[Row]]:
        """Pages of the master's relation.

        Args:
            spec (MasterSpec): Checked identifiers of the master.
            batch_size (int): Rows per page.

        Yields:
            list[Row]: One page, in key order.
        """

        selected = ", ".join(f'"{column}"' for column in spec.columns)
        relation = f'"{spec.schema}"."{spec.relation}"'
        key = f'"{spec.key}"'
        first = f"SELECT {selected} FROM {relation} ORDER BY {key} ASC LIMIT $1"
        after = f"SELECT {selected} FROM {relation} WHERE {key} > $1 ORDER BY {key} ASC LIMIT $2"
        rows = await self._fetch(first, batch_size)
        while rows:
            yield [dict(row) for row in rows]
            if len(rows) < batch_size:
                return
            rows = await self._fetch(after, rows[-1][spec.key], batch_size)
