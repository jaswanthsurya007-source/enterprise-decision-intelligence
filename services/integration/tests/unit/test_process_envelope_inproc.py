"""End-to-end L2 over the in-proc bus -- process_envelope + outbox relay, no infra.

Exercises the real consumer entrypoint (:func:`process_envelope`) and the
:class:`StreamConsumer` wrapper against the in-process :class:`EventSink` /
:class:`MessageSource` (the laptop/test bus), the in-memory
:class:`IntegrationRepo`, and the in-memory outbox adapter. The flow mirrors prod:

    publish IngestEnvelope -> edis.raw.sales.v1
      -> StreamConsumer (process_envelope: upsert + derive metrics + stage outbox)
      -> outbox relay publishes the staged events

and we assert that a :class:`CanonicalEvent` lands on ``edis.canonical.order.v1``,
a :class:`MetricPoint` (``revenue``) on ``edis.metrics.points.v1``, and a
:class:`LineageEvent` on ``edis.governance.lineage.v1`` -- all tenant-scoped.

A re-published duplicate envelope is a no-op: the idempotency guard returns a
``DUPLICATE`` outcome and writes no second canonical row (idempotent replay).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from edis_contracts import topics
from edis_contracts.events import CanonicalEvent, LineageEvent, MetricPoint
from edis_contracts.ingest import IngestEnvelope
from edis_platform.bus.base import parse_message

from edis_integration.consumers.stream_consumer import StreamConsumer
from edis_integration.mappers.identity import canonical_order_id
from edis_integration.pipeline.engine import (
    IntegrationOutcome,
    process_envelope,
)

_TENANT = "acme"
_SOURCE = "simulator"


def _sales_envelope(order_id: str = "SO-100", *, amount_unit: float = 129.0) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=uuid4(),
        idempotency_key=f"sales:{_TENANT}:{_SOURCE}:{order_id}",
        schema_ref="sales.v1",
        domain="sales",
        tenant_id=_TENANT,
        source_system=_SOURCE,
        ingest_ts=datetime.now(timezone.utc),
        event_ts=datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc),
        anomaly_label=None,
        payload={
            "order_id": order_id,
            "customer_id": "C1",
            "sku": "SKU-A",
            "qty": 2,
            "unit_price": amount_unit,
            "currency": "USD",
            "region": "EMEA",
            "channel": "web",
            "order_ts": "2026-06-12T10:00:00Z",
        },
    )


async def _drain(stream, *, idle: float = 0.4) -> list:
    """Collect messages off an in-proc subscription until it goes idle."""

    out = []
    while True:
        try:
            msg = await asyncio.wait_for(stream.__anext__(), timeout=idle)
        except asyncio.TimeoutError:
            return out
        out.append(msg)


# ---------------------------------------------------------------------------
# process_envelope directly (no bus) -- staged outbox shape
# ---------------------------------------------------------------------------
async def test_process_envelope_persists_and_stages_outbox(repo) -> None:
    res = await process_envelope(_sales_envelope(), repo=repo)

    assert res.outcome is IntegrationOutcome.PERSISTED
    assert len(res.orders) == 1
    assert len(res.customers) == 1
    # revenue + orders metric observations.
    assert {m.metric_key for m in res.metrics} == {"revenue", "orders"}

    # the canonical row is committed to the in-memory store.
    ord_id = canonical_order_id(_TENANT, _SOURCE, "SO-100")
    assert ord_id in repo.orders

    # outbox staged: order canonical event + customer canonical event +
    # one metric point per observation + one lineage edge.
    topics_staged = [ev.topic for ev in res.outbox]
    assert topics.CANONICAL_ORDER in topics_staged
    assert topics.CANONICAL_CUSTOMER in topics_staged
    assert topics_staged.count(topics.METRICS_POINTS) == 2
    assert topics_staged.count(topics.LINEAGE) == 1


# ---------------------------------------------------------------------------
# Full stream-consumer + relay over the in-proc bus
# ---------------------------------------------------------------------------
async def test_stream_consumer_publishes_canonical_metric_lineage(
    sink, source, repo, outbox_reader
) -> None:
    # Subscribe the downstream consumers FIRST (the in-proc bus registers queues
    # eagerly at subscribe() time, so a later publish is delivered).
    canon_stream = source.subscribe([topics.CANONICAL_ORDER], group="canon-consumer")
    metric_stream = source.subscribe([topics.METRICS_POINTS], group="metric-consumer")
    lineage_stream = source.subscribe([topics.LINEAGE], group="lineage-consumer")
    raw_stream = source.subscribe([topics.RAW_SALES], group="edis-integration")

    consumer = StreamConsumer(source, sink, repo, outbox_reader)
    consumer.subscribe()  # register the raw subscription before publishing

    # Publish the raw envelope L1 would have produced.
    env = _sales_envelope()
    await sink.publish(topics.RAW_SALES, key=env.tenant_id, value=env)

    # Run the consumer for exactly one message (it processes + relays).
    processed = await consumer.run(max_messages=1)
    assert processed == 1
    assert consumer.persisted == 1

    # 1. canonical order change event.
    canon_msgs = await _drain(canon_stream)
    assert len(canon_msgs) == 1
    ce = parse_message(canon_msgs[0])
    assert isinstance(ce, CanonicalEvent)
    assert ce.entity == "order"
    assert ce.op == "created"
    assert ce.tenant_id == _TENANT
    assert ce.canonical_id == canonical_order_id(_TENANT, _SOURCE, "SO-100")
    assert canon_msgs[0].key == str(ce.canonical_id)

    # 2. metric points -- revenue value = amount_base = 129*2 = 258.
    metric_msgs = await _drain(metric_stream)
    points = [parse_message(m) for m in metric_msgs]
    assert all(isinstance(p, MetricPoint) for p in points)
    by_key = {p.metric_key: p for p in points}
    assert by_key["revenue"].value == 258.0
    assert by_key["revenue"].unit == "USD"
    assert by_key["revenue"].dimensions == {"region": "EMEA", "channel": "web"}
    assert by_key["orders"].value == 1.0

    # 3. lineage edge raw_event -> canonical + metric outputs.
    lineage_msgs = await _drain(lineage_stream)
    assert len(lineage_msgs) == 1
    le = parse_message(lineage_msgs[0])
    assert isinstance(le, LineageEvent)
    assert le.stage == "integration"
    assert le.tenant_id == _TENANT
    assert {"type": "raw_event", "id": str(env.event_id)} in le.inputs
    out_types = {o["type"] for o in le.outputs}
    assert "canonical_order" in out_types
    assert "metric_observation" in out_types

    # the raw envelope was consumed by the integration group only.
    consumed = await _drain(raw_stream, idle=0.2)
    assert consumed == []


async def test_duplicate_envelope_is_idempotent_no_double_canonical_row(
    sink, source, repo, outbox_reader
) -> None:
    canon_stream = source.subscribe([topics.CANONICAL_ORDER], group="canon-dup")

    consumer = StreamConsumer(source, sink, repo, outbox_reader)
    consumer.subscribe()

    env = _sales_envelope()
    # publish the SAME envelope twice (replay).
    await sink.publish(topics.RAW_SALES, key=env.tenant_id, value=env)
    await sink.publish(topics.RAW_SALES, key=env.tenant_id, value=env)

    await consumer.run(max_messages=2)

    assert consumer.persisted == 1
    assert consumer.duplicates == 1
    # exactly one canonical order row -- no double count.
    assert len(repo.orders) == 1
    assert len(repo.customers) == 1
    # only one revenue + one orders observation (the duplicate staged nothing).
    assert sum(1 for m in repo.metrics if m.metric_key == "revenue") == 1

    # exactly one canonical event was published (the first pass only).
    canon_msgs = await _drain(canon_stream)
    assert len(canon_msgs) == 1


async def test_duplicate_via_process_envelope_returns_duplicate(repo) -> None:
    env = _sales_envelope("SO-DUP")
    first = await process_envelope(env, repo=repo)
    second = await process_envelope(env, repo=repo)
    assert first.outcome is IntegrationOutcome.PERSISTED
    assert second.outcome is IntegrationOutcome.DUPLICATE
    # the duplicate staged no new outbox events.
    assert second.outbox == []
    assert len(repo.orders) == 1


async def test_quarantine_routes_to_dlq_no_canonical_row(repo, sink, source) -> None:
    # An order with non-positive amount is a hard DQ failure -> QUARANTINED.
    env = IngestEnvelope(
        event_id=uuid4(),
        idempotency_key="sales:acme:simulator:SO-BAD",
        schema_ref="sales.v1",
        domain="sales",
        tenant_id=_TENANT,
        source_system=_SOURCE,
        ingest_ts=datetime.now(timezone.utc),
        event_ts=datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc),
        payload={
            "order_id": "SO-BAD",
            "customer_id": "C1",
            "sku": "SKU-A",
            "qty": 1,
            "unit_price": 0.0,  # amount_base = 0 -> hard DQ failure
            "currency": "USD",
            "region": "EMEA",
            "channel": "web",
            "order_ts": "2026-06-12T10:00:00Z",
        },
    )
    res = await process_envelope(env, repo=repo)
    assert res.outcome is IntegrationOutcome.QUARANTINED
    assert res.quarantine is not None
    assert res.quarantine.stage == "integration"
    assert res.quarantine.tenant_id == _TENANT
    # nothing was written to the canonical store.
    assert repo.orders == {}
    # the DLQ event is staged for direct publication.
    assert [ev.topic for ev in res.outbox] == [topics.DLQ_INTEGRATION]
