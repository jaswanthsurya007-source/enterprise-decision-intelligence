"""Alembic environment for the ingestion service (async).

The database URL comes from the shared ``edis_platform`` settings
(``EDIS_DATABASE_URL``), not from ``alembic.ini``, so migrations always target the
same database as the running service. ``target_metadata`` is the shared
:class:`edis_platform.db.session.Base` metadata with the ingestion models imported
so autogenerate sees ``raw_events`` / ``ingest_dlq`` / ``ingest_checkpoint``.
"""

from __future__ import annotations

import asyncio

from alembic import context
from edis_platform.db.session import Base
from edis_platform.settings import get_settings
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

# Import models so their tables register on Base.metadata.
import ingestion.storage.models  # noqa: F401

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Emit SQL without a live DBAPI connection."""

    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live async engine."""

    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    engine = async_engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool, future=True
    )
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
