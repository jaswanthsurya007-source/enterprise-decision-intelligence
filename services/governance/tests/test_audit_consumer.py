"""End-to-end audit-consumer test against a real Postgres (Docker required).

Marked ``@pytest.mark.integration`` so ``pytest -m "not integration"`` skips it on
a laptop / in CI without Docker. It stands up a throwaway Postgres via
``testcontainers``, creates the governance schema from the ORM metadata, and runs
the **real** :class:`AuditConsumer` over the **real** in-process bus:

* publish one :class:`AuditEvent` -> exactly one ``audit_log`` row;
* publish the byte-identical event again -> still one row (``ON CONFLICT
  (audit_id) DO NOTHING`` dedupe), and the consumer reports ``inserted=False``.

This is the Postgres-backed proof of the dedupe contract that
``test_audit_idempotency.py`` pins with an in-memory fake.

Notes:
* TimescaleDB-specific DDL (the ``audit_log`` hypertable) is *not* required here:
  the ORM ``create_all`` yields a plain Postgres table whose ``audit_id`` PK /
  unique constraint is exactly the column the ``ON CONFLICT`` targets, so the
  idempotent insert behaves identically.
* The engine/sessionmaker singletons in :mod:`edis_platform.db.session` are reset
  and pointed at the container DSN for the duration of the test.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

# testcontainers is a dev-only extra; if it (or Docker) is missing, skip cleanly
# rather than error at collection time.
testcontainers_postgres = pytest.importorskip(
    "testcontainers.postgres",
    reason="testcontainers[postgres] not installed (Docker-only integration test)",
)
PostgresContainer = testcontainers_postgres.PostgresContainer

from edis_contracts import topics  # noqa: E402
from edis_contracts.governance import AuditEvent  # noqa: E402
from edis_platform.bus import make_sink, make_source  # noqa: E402
from edis_platform.bus.inproc import reset_brokers  # noqa: E402
from edis_platform.db import session as db_session  # noqa: E402
from edis_platform.settings import Settings  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.consumers.audit_consumer import AuditConsumer  # noqa: E402
from app.models import AuditLog  # noqa: E402


def _async_dsn(container: "PostgresContainer") -> str:
    """Return the container's connection URL as an asyncpg async DSN."""

    url = container.get_connection_url()  # postgresql+psycopg2://... or postgresql://...
    # Normalize whatever sync driver testcontainers picked to asyncpg.
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url


def _audit_event(audit_id, *, action: str = "DATA_WRITE") -> AuditEvent:
    return AuditEvent(
        audit_id=audit_id,
        occurred_at=datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc),
        tenant_id="tenant-a",
        actor={"type": "user", "id": "user-1", "roles": ["operator"]},
        action=action,  # type: ignore[arg-type]
        resource={"type": "metric", "id": "revenue"},
        outcome="ALLOW",
        trace_id="trace-int",
    )


@pytest.fixture
def _isolated_broker_int():
    reset_brokers()
    yield
    reset_brokers()


@pytest.fixture
async def pg_settings():
    """Boot a Postgres container, create the schema, and yield wired Settings.

    The engine/sessionmaker singletons used by the consumer are reset and rebound
    to the container DSN so the consumer's lazy ``get_sessionmaker()`` resolves to
    this database.
    """

    with PostgresContainer("postgres:16-alpine") as container:
        dsn = _async_dsn(container)
        settings = Settings(sink_backend="inproc", database_url=dsn)

        # Point the platform's lazy singletons at this DB.
        db_session.reset_engine()
        engine = db_session.make_engine(settings)
        db_session._engine = engine
        db_session._sessionmaker = db_session.make_sessionmaker(engine)

        # Create the governance schema from the ORM metadata (plain-PG flavor).
        async with engine.begin() as conn:
            await conn.run_sync(db_session.Base.metadata.create_all)

        try:
            yield settings
        finally:
            async with engine.begin() as conn:
                await conn.run_sync(db_session.Base.metadata.drop_all)
            await engine.dispose()
            db_session.reset_engine()


async def _count_rows() -> int:
    sessionmaker = db_session.get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(select(func.count()).select_from(AuditLog))
        return int(result.scalar_one())


async def test_audit_event_persisted_once_and_dedup(pg_settings, _isolated_broker_int) -> None:
    """One published AuditEvent -> one row; a duplicate publish is a no-op."""

    settings = pg_settings
    sink = make_sink(settings)
    source = make_source(settings)

    consumer = AuditConsumer(settings, source=source)
    consumer_task = asyncio.create_task(consumer.run())
    await asyncio.sleep(0.1)  # let the consumer subscribe before we publish

    await sink.start()
    audit_id = uuid4()
    event = _audit_event(audit_id)

    # Publish the same event twice (at-least-once redelivery simulation).
    await sink.publish(topics.AUDIT, key=event.tenant_id, value=event)
    await sink.publish(topics.AUDIT, key=event.tenant_id, value=event)

    # Wait for both deliveries to be processed (poll the row count).
    for _ in range(50):
        await asyncio.sleep(0.1)
        if await _count_rows() >= 1:
            break

    await consumer.stop()
    consumer_task.cancel()
    try:
        await consumer_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    await sink.stop()

    # Exactly one row, keyed by the idempotent audit_id.
    assert await _count_rows() == 1

    sessionmaker = db_session.get_sessionmaker()
    async with sessionmaker() as session:
        row = (
            await session.execute(select(AuditLog).where(AuditLog.audit_id == audit_id))
        ).scalar_one()
        assert row.tenant_id == "tenant-a"
        assert row.action == "DATA_WRITE"
        assert row.outcome == "ALLOW"
        assert row.raw["audit_id"] == str(audit_id)


async def test_direct_repo_insert_is_idempotent(pg_settings, _isolated_broker_int) -> None:
    """The repository insert itself dedupes on audit_id against real Postgres."""

    from app.repo import AuditRepository

    sessionmaker = db_session.get_sessionmaker()
    event = _audit_event(uuid4())

    async with sessionmaker() as session:
        repo = AuditRepository(session)
        assert await repo.insert(event) is True  # new row
        assert await repo.insert(event) is False  # ON CONFLICT DO NOTHING
        await session.commit()

    assert await _count_rows() == 1
