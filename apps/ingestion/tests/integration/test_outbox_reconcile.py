"""Integration: the raw_events outbox + reconcile relay against real Postgres.

Marked ``@pytest.mark.integration`` so it is excluded from the unit suite and runs
only with Docker + testcontainers. It brings up a Postgres container, creates the
ingestion tables off the shared :class:`edis_platform.db.session.Base` metadata,
and exercises the durable-write side of the outbox pattern with the real
:class:`~ingestion.storage.raw_writer.RawWriter`:

1. ``ingest_record`` with ``publish_after_land=False`` lands a ``raw_events`` row
   but does *not* publish — simulating a crash/broker-outage after the durable
   write (the row is ``published=false``).
2. :func:`ingestion.storage.relay.reconcile` re-reads the unpublished row, rebuilds
   the :class:`IngestEnvelope`, republishes it through the in-proc publisher, and
   flips ``published=true`` — proving there is no "persisted-but-not-published" gap.
3. The DB unique ``idempotency_key`` is the final dedupe backstop: a second land of
   the same record returns "not newly written" and does not double-publish.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

testcontainers_postgres = pytest.importorskip(
    "testcontainers.postgres", reason="testcontainers[postgres] not installed"
)
PostgresContainer = testcontainers_postgres.PostgresContainer


@pytest.fixture(scope="module")
def postgres():
    # asyncpg driver URL for SQLAlchemy async engine.
    with PostgresContainer("postgres:16", driver="asyncpg") as pg:
        yield pg


@pytest.fixture
async def sessionmaker_fix(postgres):
    from edis_platform.db.session import Base, make_engine, make_sessionmaker
    from edis_platform.settings import Settings

    url = postgres.get_connection_url()  # postgresql+asyncpg://...
    settings = Settings(database_url=url)
    engine = make_engine(settings)

    # Create only the ingestion tables (import registers them on Base.metadata).
    import ingestion.storage.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield make_sessionmaker(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def _capturing_publisher():
    from ingestion.publish.publisher import IngestPublisher

    class _Sink:
        def __init__(self):
            self.published = []

        async def start(self): ...

        async def stop(self): ...

        async def publish(self, topic, key, value):
            self.published.append((topic, key, value))

    sink = _Sink()
    return IngestPublisher(sink), sink


_RAW = {
    "order_id": "SO-OB-1",
    "customer_id": "C1",
    "sku": "SKU-A",
    "qty": "2",
    "unit_price": "$129.00",
    "region": "EMEA",
    "channel": "web",
    "ts": "06/12/2026",
}


async def test_land_without_publish_then_reconcile(sessionmaker_fix):
    from edis_contracts import topics
    from sqlalchemy import select

    from ingestion.pipeline.engine import IngestOutcome, ingest_record
    from ingestion.pipeline.idempotency import InMemoryIdempotencyStore
    from ingestion.storage.models import RawEvent
    from ingestion.storage.raw_writer import RawWriter
    from ingestion.storage.relay import reconcile

    writer = RawWriter(sessionmaker_fix)
    publisher, sink = _capturing_publisher()
    idem = InMemoryIdempotencyStore()

    # 1. land durably but do NOT publish (simulating broker outage post-land).
    res = await ingest_record(
        "sales",
        dict(_RAW),
        tenant_id="acme",
        source_system="simulator",
        ctx_sink=publisher,
        idem=idem,
        writer=writer,
        publish_after_land=False,
    )
    assert res.outcome is IngestOutcome.LANDED
    assert res.published is False
    assert sink.published == []  # nothing on the bus yet

    # the row exists and is unpublished.
    async with sessionmaker_fix() as session:
        rows = (await session.execute(select(RawEvent))).scalars().all()
    assert len(rows) == 1
    assert rows[0].published is False
    assert rows[0].idempotency_key == "sales:acme:simulator:SO-OB-1"

    # 2. reconcile republishes and flips the flag.
    n = await reconcile(writer, publisher)
    assert n == 1
    raw_published = [p for p in sink.published if p[0] == topics.RAW_SALES]
    assert len(raw_published) == 1
    assert raw_published[0][1] == "acme"  # keyed by tenant_id

    # the row is now published; a second reconcile is a no-op.
    async with sessionmaker_fix() as session:
        rows = (await session.execute(select(RawEvent))).scalars().all()
    assert rows[0].published is True
    assert await reconcile(writer, publisher) == 0


async def test_db_unique_key_is_dedupe_backstop(sessionmaker_fix):
    from ingestion.pipeline.engine import IngestOutcome, ingest_record
    from ingestion.pipeline.idempotency import InMemoryIdempotencyStore
    from ingestion.storage.raw_writer import RawWriter

    writer = RawWriter(sessionmaker_fix)
    publisher, sink = _capturing_publisher()

    # Two *separate* in-memory guards (e.g. two replicas) both pass their guard,
    # so the DB unique constraint must be the final dedupe backstop.
    res1 = await ingest_record(
        "sales",
        dict(_RAW),
        tenant_id="acme",
        source_system="simulator",
        ctx_sink=publisher,
        idem=InMemoryIdempotencyStore(),
        writer=writer,
    )
    res2 = await ingest_record(
        "sales",
        dict(_RAW),
        tenant_id="acme",
        source_system="simulator",
        ctx_sink=publisher,
        idem=InMemoryIdempotencyStore(),
        writer=writer,
    )

    assert res1.outcome is IngestOutcome.LANDED
    assert res2.outcome is IngestOutcome.DUPLICATE  # absorbed by the unique key
    assert res2.published is False
