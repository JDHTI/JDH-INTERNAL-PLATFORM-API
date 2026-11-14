"""Alembic environment configured for async PostgreSQL."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.infrastructure.models_registry import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# 0014_masters builds its search indexes with pg_trgm when the extension is
# available and falls back to lower() btree indexes otherwise, so the models
# cannot declare them. Without this, autogenerate and ``alembic check`` would
# ask to drop whichever pair the database ended up with.
_ENVIRONMENT_DEPENDENT_INDEXES = frozenset(
    {
        "ix_master_records_code_trgm",
        "ix_master_records_name_trgm",
        "ix_master_records_code_lower",
        "ix_master_records_name_lower",
    }
)


# Tables the PostGIS extension creates (0063): they belong to the extension, not to a model.
_EXTENSION_TABLES = frozenset({"spatial_ref_sys"})


def include_object(object_, name, type_, reflected, compare_to) -> bool:  # noqa: ANN001
    if type_ == "table" and reflected and name in _EXTENSION_TABLES:
        return False
    return not (type_ == "index" and reflected and name in _ENVIRONMENT_DEPENDENT_INDEXES)


def run_migrations_offline() -> None:
    """Run migrations without creating a database connection."""

    context.configure(
        url=settings.DATABASE_URL.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = settings.DATABASE_URL.get_secret_value()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
