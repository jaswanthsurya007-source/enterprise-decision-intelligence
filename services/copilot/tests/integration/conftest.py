"""Fixtures for the copilot **integration** suite (``@pytest.mark.integration``).

These exercise the real async-SQLAlchemy :class:`CopilotAnswerRepository` against a live
Postgres instance, so they require Docker and are excluded from ``pytest -m "not
integration"``. Everything testcontainer-specific is imported lazily inside the fixtures
so this module imports cleanly (and the unit suite collects) with no ``testcontainers``
and no Docker.

The copilot's own ``copilot_conversation`` / ``copilot_answer`` tables are created
directly from the shared ORM metadata (``Base.metadata.create_all`` for just those two
tables) rather than via Alembic — the copilot ships no migrations in this phase, and the
ORM is the typed access layer under test. The DataPort reads (metric/findings/pgvector)
have their own integration coverage against the L2/L3 schemas and are not re-tested here.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

_PG_IMAGE = "pgvector/pgvector:pg16"


@pytest.fixture(scope="session")
def _pg_container():
    """Start a Postgres container for the session (skips if Docker/testcontainers absent)."""

    try:
        from testcontainers.postgres import PostgresContainer
    except Exception as exc:  # pragma: no cover - infra-dependent
        pytest.skip(f"testcontainers not available: {exc}")

    try:
        container = PostgresContainer(
            _PG_IMAGE, username="edis", password="edis", dbname="edis", driver="asyncpg"
        )
        container.start()
    except Exception as exc:  # pragma: no cover - infra-dependent
        pytest.skip(f"could not start Postgres container (Docker?): {exc}")
    try:
        yield container
    finally:
        container.stop()


@pytest_asyncio.fixture
async def pg_sessionmaker(_pg_container):
    """A fresh async sessionmaker with the copilot ORM tables created from metadata."""

    from edis_platform.db.session import Base, make_engine, make_sessionmaker
    from edis_platform.settings import Settings

    # Import the ORM so its tables register on the shared Base.metadata.
    from edis_copilot.persistence.models import (
        CopilotAnswerRow,
        CopilotConversationRow,
    )  # noqa: F401

    engine = make_engine(Settings(database_url=_pg_container.get_connection_url()))
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[CopilotConversationRow.__table__, CopilotAnswerRow.__table__],
            )
        )
    sm = make_sessionmaker(engine)
    try:
        yield sm
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda c: Base.metadata.drop_all(
                    c,
                    tables=[CopilotAnswerRow.__table__, CopilotConversationRow.__table__],
                )
            )
        await engine.dispose()
