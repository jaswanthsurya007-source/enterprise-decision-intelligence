"""Alembic environment for the EDIS governance service (L7).

Reads the async database URL from ``EDIS_DATABASE_URL`` so one set of migrations
applies in CI, docker-compose, and local dev without editing ``alembic.ini``.
Migrations run against an async SQLAlchemy engine (asyncpg) via
:func:`run_async_migrations`; no engine is created at import time. These are
hand-authored raw DDL (the governance tables are owned here; the G1 ORM models
must MATCH this schema exactly), so there is no reflected ``target_metadata``.
"""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

target_metadata = None

_DEFAULT_URL = "postgresql+asyncpg://edis:edis@localhost:5432/edis"


def _database_url() -> str:
    return os.getenv("EDIS_DATABASE_URL", config.get_main_option("sqlalchemy.url") or _DEFAULT_URL)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL, no DB connection)."""

    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
