"""Async SQLAlchemy engine and session management."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for models owned by application modules."""


class Database:
    """Own the async engine and produce short-lived sessions."""

    def __init__(
        self,
        database_url: str,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_timeout: int = 10,
        connect_timeout: float = 2.0,
    ) -> None:
        self.engine: AsyncEngine = create_async_engine(
            database_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            connect_args={"timeout": connect_timeout},
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    async def check(self) -> None:
        """Verify that PostgreSQL accepts a lightweight query."""

        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Yield a session and always close it after the operation."""

        async with self.session_factory() as session:
            yield session

    async def dispose(self) -> None:
        """Release all pooled connections."""

        await self.engine.dispose()
