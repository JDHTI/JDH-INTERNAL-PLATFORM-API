"""Connection lifecycle for external data providers."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import asyncpg


class DataProviderError(Exception):
    """Base error raised while using an external data provider."""


class DataProviderUnavailable(DataProviderError):
    """Raised when an external provider is disabled or cannot be reached."""


class DataProviderConnection:
    """Manage a lazy, read-only connection pool for one configured provider."""

    def __init__(
        self,
        provider: str,
        enabled: bool,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        min_size: int = 1,
        max_size: int = 5,
        connect_timeout: float = 2.0,
        command_timeout: float = 10.0,
    ) -> None:
        self.provider = provider.strip().lower()
        self.enabled = enabled
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.min_size = min_size
        self.max_size = max_size
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self._pool: asyncpg.Pool | None = None
        self._pool_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Open the provider pool the first time it is needed.

        Raises:
            DataProviderUnavailable: If the provider is disabled, unsupported,
                or the pool cannot be opened.
        """

        if not self.enabled:
            raise DataProviderUnavailable("Data provider is disabled")
        if self.provider != "postgres":
            raise DataProviderUnavailable(f"Unsupported data provider: {self.provider}")
        if self._pool is not None:
            return

        async with self._pool_lock:
            if self._pool is not None:
                return
            try:
                self._pool = await asyncpg.create_pool(
                    host=self.host,
                    port=self.port,
                    database=self.database,
                    user=self.user,
                    password=self.password,
                    min_size=self.min_size,
                    max_size=self.max_size,
                    timeout=self.connect_timeout,
                    command_timeout=self.command_timeout,
                )
            except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
                raise DataProviderUnavailable("Unable to connect to data provider") from exc

    async def check(self) -> None:
        """Verify that the selected provider accepts a read-only query."""

        await self.connect()
        pool = self._pool
        if pool is None:  # pragma: no cover - connect() either sets or raises
            raise DataProviderUnavailable("Data provider pool is not initialized")
        try:
            async with pool.acquire() as connection:
                async with connection.transaction(readonly=True):
                    await connection.fetchval("SELECT 1")
        except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
            raise DataProviderUnavailable("Data provider health check failed") from exc

    async def fetch(self, query: str, *args: object) -> Sequence[asyncpg.Record]:
        """Execute a parameterized read-only query on the provider.

        Args:
            query (str): SQL statement with asyncpg positional parameters.
            *args (object): Values bound to the SQL statement.

        Returns:
            Sequence[asyncpg.Record]: Rows returned by the provider.

        Raises:
            DataProviderUnavailable: If the provider cannot execute the query.
        """

        await self.connect()
        pool = self._pool
        if pool is None:  # pragma: no cover - connect() either sets or raises
            raise DataProviderUnavailable("Data provider pool is not initialized")
        try:
            async with pool.acquire() as connection:
                async with connection.transaction(readonly=True):
                    return await connection.fetch(query, *args)
        except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
            raise DataProviderUnavailable("Data provider query failed") from exc

    async def disconnect(self) -> None:
        """Close the provider pool and release all open connections."""

        pool, self._pool = self._pool, None
        if pool is not None:
            await pool.close()

    async def close(self) -> None:
        """Alias used by the application lifecycle to release this provider."""

        await self.disconnect()
