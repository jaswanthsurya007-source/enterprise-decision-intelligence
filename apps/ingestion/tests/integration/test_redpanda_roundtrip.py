"""Integration: ingest -> Redpanda (Kafka API) -> consume, end to end.

Marked ``@pytest.mark.integration`` so it is **excluded** from the unit suite
(``pytest -m "not integration"``) and runs only where Docker + testcontainers are
available. It spins up a single Redpanda container, points the platform settings'
``sink_backend="kafka"`` at it, runs a sales record through the real
:func:`ingestion.pipeline.engine.ingest_record` (in-memory idempotency, no DB),
and asserts the :class:`IngestEnvelope` actually round-trips the broker on
``edis.raw.sales.v1`` keyed by ``tenant_id`` — and that the ``DATA_WRITE``
:class:`AuditEvent` lands on ``edis.governance.audit.v1``.

This proves the in-proc fallback and the real broker are behaviorally identical
behind the :class:`EventSink`/:class:`MessageSource` ports (the same assertions as
``test_pipeline_inproc`` but over Kafka).
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

testcontainers_redpanda = pytest.importorskip(
    "testcontainers.redpanda", reason="testcontainers[redpanda] not installed"
)
RedpandaContainer = testcontainers_redpanda.RedpandaContainer


@pytest.fixture(scope="module")
def redpanda():
    with RedpandaContainer() as container:
        yield container


@pytest.fixture
def kafka_settings(redpanda):
    from edis_platform.settings import Settings

    bootstrap = redpanda.get_bootstrap_server()
    return Settings(sink_backend="kafka", kafka_bootstrap_servers=bootstrap)


async def _consume_one(source, topic: str, group: str, timeout: float = 30.0):
    stream = source.subscribe([topic], group=group)
    return await asyncio.wait_for(stream.__anext__(), timeout=timeout)


async def test_sales_envelope_roundtrips_redpanda(kafka_settings):
    from edis_contracts import topics
    from edis_contracts.ingest import IngestEnvelope
    from edis_platform.bus.base import make_sink, make_source, parse_message

    from ingestion.pipeline.engine import ingest_record
    from ingestion.pipeline.idempotency import InMemoryIdempotencyStore
    from ingestion.publish.publisher import IngestPublisher

    sink = make_sink(kafka_settings)
    source = make_source(kafka_settings)
    await sink.start()
    await source.start()
    try:
        publisher = IngestPublisher(sink)
        idem = InMemoryIdempotencyStore()

        # Start the consumer first so it is positioned before we publish.
        raw_stream = source.subscribe([topics.RAW_SALES], group="it-raw")
        await asyncio.sleep(1.0)

        res = await ingest_record(
            "sales",
            {
                "order_id": "SO-IT-1",
                "customer_id": "C1",
                "sku": "SKU-A",
                "qty": "2",
                "unit_price": "$129.00",
                "region": "EMEA",
                "channel": "web",
                "ts": "06/12/2026",
            },
            tenant_id="acme",
            source_system="simulator",
            ctx_sink=publisher,
            idem=idem,
            writer=None,
        )
        assert res.published is True

        msg = await asyncio.wait_for(raw_stream.__anext__(), timeout=30.0)
        assert msg.topic == topics.RAW_SALES
        assert msg.key == "acme"
        env = parse_message(msg)
        assert isinstance(env, IngestEnvelope)
        assert env.idempotency_key == "sales:acme:simulator:SO-IT-1"
        assert env.payload["unit_price"] == 129.0
    finally:
        await source.stop()
        await sink.stop()


async def test_audit_event_roundtrips_redpanda(kafka_settings):
    from edis_contracts import topics
    from edis_contracts.governance import AuditEvent
    from edis_platform.bus.base import make_sink, make_source, parse_message

    from ingestion.pipeline.engine import ingest_record
    from ingestion.pipeline.idempotency import InMemoryIdempotencyStore
    from ingestion.publish.publisher import IngestPublisher

    sink = make_sink(kafka_settings)
    source = make_source(kafka_settings)
    await sink.start()
    await source.start()
    try:
        publisher = IngestPublisher(sink)
        idem = InMemoryIdempotencyStore()

        audit_stream = source.subscribe([topics.AUDIT], group="it-audit")
        await asyncio.sleep(1.0)

        await ingest_record(
            "ops",
            {
                "service": "checkout-api",
                "region": "EMEA",
                "level": "error",
                "status_code": "503",
                "latency_ms": "1400",
                "message": "boom",
                "ts": "2026-06-12T10:00:00Z",
            },
            tenant_id="acme",
            source_system="erp",
            ctx_sink=publisher,
            idem=idem,
            writer=None,
        )

        msg = await asyncio.wait_for(audit_stream.__anext__(), timeout=30.0)
        audit = parse_message(msg)
        assert isinstance(audit, AuditEvent)
        assert audit.action == "DATA_WRITE"
        assert audit.outcome == "ALLOW"
        assert audit.tenant_id == "acme"
    finally:
        await source.stop()
        await sink.stop()
