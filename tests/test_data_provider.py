"""The read-only DWH pool: a dead pooled connection is retried once, never a 500."""

from __future__ import annotations

from contextlib import asynccontextmanager

import asyncpg
import pytest

from app.infrastructure.data_provider import DataProviderConnection, DataProviderUnavailable


class FakeConnection:
    def __init__(self, outcome):
        self.outcome = outcome

    @asynccontextmanager
    async def transaction(self, readonly):
        yield

    async def execute(self, *args):
        return None

    async def fetch(self, query, *args, timeout=None):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakePool:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.acquired = 0

    @asynccontextmanager
    async def acquire(self):
        self.acquired += 1
        yield FakeConnection(self.outcomes.pop(0))


def provider(pool: FakePool) -> DataProviderConnection:
    connection = DataProviderConnection("postgres", True, "h", 5432, "d", "u", "p")
    connection._pool = pool  # type: ignore[assignment]
    return connection


async def test_a_dead_connection_is_tried_once_more() -> None:
    closed = asyncpg.InterfaceError("cannot perform operation: connection is closed")
    pool = FakePool(closed, [{"x": 1}])

    rows = await provider(pool).fetch("SELECT 1", settings={"jit": "off"})

    assert rows == [{"x": 1}] and pool.acquired == 2


async def test_twice_dead_or_a_query_error_is_unavailable() -> None:
    lost = asyncpg.ConnectionDoesNotExistError("closed in the middle of operation")
    with pytest.raises(DataProviderUnavailable, match="connection lost"):
        await provider(FakePool(lost, lost)).fetch("SELECT 1")

    # A timeout or a query error is not retried: it would only double the wait.
    pool = FakePool(TimeoutError(), [])
    with pytest.raises(DataProviderUnavailable, match="query failed"):
        await provider(pool).fetch("SELECT 1")
    assert pool.acquired == 1
