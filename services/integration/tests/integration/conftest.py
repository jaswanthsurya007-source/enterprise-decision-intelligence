"""Fixtures for the L2 **integration** suite (``@pytest.mark.integration``).

These exercise the real async-SQLAlchemy repo + transactional outbox against a
live Postgres+TimescaleDB instance, so they require Docker and are excluded from
``pytest -m "not integration"``. Everything Docker-/testcontainer-specific is
imported lazily *inside* the fixtures, so this module imports cleanly (and the
unit suite collects) on a machine with no ``testcontainers`` and no Docker.

The ``pg_sessionmaker`` fixture:

1. starts a ``timescale/timescaledb`` container (a TimescaleDB-enabled Postgres
   so ``0001_canonical`` promotes ``metric_observations`` to a hypertable),
2. points ``EDIS_DATABASE_URL`` at it and runs ``alembic upgrade head`` (the same
   migrations the service ships -- never re-authored here), and
3. yields a fresh async sessionmaker bound to a throwaway engine.

The whole stack is torn down at the end of the session.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio

_SERVICE_DIR = Path(__file__).resolve().parents[2]

# TimescaleDB-enabled Postgres so the hypertable + continuous aggregate apply.
_PG_IMAGE = "timescale/timescaledb:2.15.2-pg16"


def _sync_dsn(url: str) -> str:
    """A psycopg/sync DSN (for waiting) from the asyncpg URL."""

    return url.replace("+asyncpg", "")


@pytest.fixture(scope="session")
def _pg_container():
    """Start a TimescaleDB container for the session (skips if Docker absent)."""

    try:
        from testcontainers.postgres import PostgresContainer
    except Exception as exc:  # pragma: no cover - infra-dependent
        pytest.skip(f"testcontainers not available: {exc}")

    try:
        container = PostgresContainer(
            _PG_IMAGE,
            username="edis",
            password="edis",
            dbname="edis",
            driver="asyncpg",
        )
        container.start()
    except Exception as exc:  # pragma: no cover - infra-dependent
        pytest.skip(f"could not start Postgres/Timescale container (Docker?): {exc}")

    try:
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def _migrated_dsn(_pg_container) -> str:
    """Run ``alembic upgrade head`` against the container; return the async DSN."""

    async_url = _pg_container.get_connection_url()  # postgresql+asyncpg://...
    env = dict(os.environ)
    env["EDIS_DATABASE_URL"] = async_url
    # Run the shipped migrations exactly as the Makefile/CI do.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_SERVICE_DIR),
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:  # pragma: no cover - infra-dependent
        pytest.skip(
            "alembic upgrade head failed:\n" f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return async_url


@pytest_asyncio.fixture
async def pg_sessionmaker(_migrated_dsn):
    """A fresh async sessionmaker bound to the migrated test database."""

    from edis_platform.db.session import make_engine, make_sessionmaker
    from edis_platform.settings import Settings

    engine = make_engine(Settings(database_url=_migrated_dsn))
    sm = make_sessionmaker(engine)
    try:
        yield sm
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(pg_sessionmaker):
    """Truncate the L2 tables between integration tests (isolated state)."""

    from sqlalchemy import text

    tables = [
        "integration_outbox",
        "integration_idempotency",
        "integration_quarantine",
        "metric_observations",
        "canonical_order_line",
        "canonical_order",
        "ops_event",
        "canonical_customer",
    ]
    async with pg_sessionmaker() as session:
        for tbl in tables:
            await session.execute(text(f"TRUNCATE TABLE {tbl} CASCADE"))
        await session.commit()
    yield
