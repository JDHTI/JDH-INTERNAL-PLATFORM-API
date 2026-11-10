"""The writer connection to the warehouse: its own small pool, own role and hard ceilings.

It is separate from the read-only data provider, so publishing never takes connections from
the screens that read the DWH, and the ceilings make a slow warehouse fail fast instead of
holding locks: the outbox retries later.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from app.modules.dwh_publish.domain.ports import DwhRejected, DwhUnavailable

APPLICATION_NAME = "plataforma-apoyos-worker"

# Failures that say "not now" (network, permissions or tables not ready, timeouts, a lock):
# the events wait and nothing is charged to them.
_TRANSIENT = (
    OSError,
    TimeoutError,
    asyncio.TimeoutError,
    asyncpg.exceptions.InterfaceError,
    asyncpg.exceptions.ConnectionDoesNotExistError,
    asyncpg.exceptions.CannotConnectNowError,
    asyncpg.exceptions.TooManyConnectionsError,
    asyncpg.exceptions.InvalidAuthorizationSpecificationError,
    asyncpg.exceptions.InvalidPasswordError,
    asyncpg.exceptions.InsufficientPrivilegeError,
    asyncpg.exceptions.UndefinedTableError,
    asyncpg.exceptions.InvalidSchemaNameError,
    asyncpg.exceptions.QueryCanceledError,
    asyncpg.exceptions.LockNotAvailableError,
    asyncpg.exceptions.DeadlockDetectedError,
    asyncpg.exceptions.SerializationError,
    asyncpg.exceptions.AdminShutdownError,
    asyncpg.exceptions.CrashShutdownError,
    asyncpg.exceptions.ReadOnlySQLTransactionError,
)


def classify(exc: BaseException) -> Exception:
    """The publication error an exception stands for."""

    if isinstance(exc, (DwhUnavailable, DwhRejected)):
        return exc
    if isinstance(exc, _TRANSIENT):
        return DwhUnavailable(f"{type(exc).__name__}: {exc}")
    return DwhRejected(f"{type(exc).__name__}: {exc}")


class WarehouseWriterConnection:
    """Lazy pool with the writer role (``svc_plataforma_apoyos``)."""

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        ssl: str = "require",
        max_size: int = 2,
        connect_timeout: float = 5.0,
        statement_timeout_ms: int = 60_000,
        lock_timeout_ms: int = 10_000,
        idle_in_transaction_timeout_ms: int = 60_000,
    ) -> None:
        """Create the connection settings; nothing is opened until the first write.

        Args:
            host (str): Warehouse host.
            port (int): Port.
            database (str): Database.
            user (str): Writer role.
            password (str): Its password.
            ssl (str): ``require``, ``verify-full``… or ``disable`` (local tests only).
            max_size (int): Most connections (the role allows 5).
            connect_timeout (float): Seconds to open a connection.
            statement_timeout_ms (int): Longest statement.
            lock_timeout_ms (int): Longest wait for a lock.
            idle_in_transaction_timeout_ms (int): Longest idle open transaction.
        """

        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.ssl = ssl
        self.max_size = max_size
        self.connect_timeout = connect_timeout
        self.server_settings = {
            "application_name": APPLICATION_NAME,
            "statement_timeout": str(statement_timeout_ms),
            "lock_timeout": str(lock_timeout_ms),
            "idle_in_transaction_session_timeout": str(idle_in_transaction_timeout_ms),
        }
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        """Whether a host and a role were given."""

        return bool(self.host and self.user)

    async def _connect(self) -> asyncpg.Pool:
        if not self.configured:
            raise DwhUnavailable("La conexión de escritura al DWH no está configurada")
        if self._pool is not None:
            return self._pool
        async with self._lock:
            if self._pool is None:
                try:
                    self._pool = await asyncpg.create_pool(
                        host=self.host,
                        port=self.port,
                        database=self.database,
                        user=self.user,
                        password=self.password,
                        ssl=None if self.ssl == "disable" else self.ssl,
                        min_size=0,
                        max_size=self.max_size,
                        timeout=self.connect_timeout,
                        command_timeout=self.connect_timeout
                        + int(self.server_settings["statement_timeout"]) / 1000,
                        server_settings=self.server_settings,
                    )
                except BaseException as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    raise classify(exc) from exc
        return self._pool

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        """One warehouse transaction; any failure comes out as DwhUnavailable or DwhRejected."""

        pool = await self._connect()
        try:
            async with pool.acquire() as connection, connection.transaction():
                yield connection
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise classify(exc) from exc

    async def close(self) -> None:
        """Close the pool."""

        pool, self._pool = self._pool, None
        if pool is not None:
            await pool.close()


__all__ = ["APPLICATION_NAME", "WarehouseWriterConnection", "classify"]
