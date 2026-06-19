"""Full pipeline over the in-proc bus — publish + audit, end to end, no infra.

This exercises the real :func:`ingestion.pipeline.engine.ingest_record` against the
real in-process :class:`EventSink`/:class:`MessageSource` (the laptop/test bus) and
the real :class:`IngestPublisher` (topics + keys + the governance audit emission).
It is the closest unit-level approximation of the production path: a record is
coerced, validated, keyed, enveloped, and published — and the very same write
emits an :class:`AuditEvent` (``action="DATA_WRITE"``) onto ``edis.governance.audit.v1``.

We subscribe a real consumer group on the in-proc broker and assert:

* a sales record lands on ``edis.raw.sales.v1`` keyed by ``tenant_id``, carrying the
  full :class:`IngestEnvelope`;
* an ops record lands on ``edis.raw.ops.v1`` keyed ``tenant_id|service``;
* an :class:`AuditEvent` (``DATA_WRITE`` / ``ALLOW``) for the same write lands on
  ``edis.governance.audit.v1``, tenant-scoped;
* a duplicate does not re-publish to the raw topic.
"""

from __future__ import annotations

import asyncio

import pytest
from edis_contracts import topics
from edis_contracts.governance import AuditEvent
from edis_contracts.ingest import IngestEnvelope
from edis_platform.bus.base import parse_message

from ingestion.pipeline.engine import IngestOutcome, ingest_record

_SALES_RAW = {
    "order_id": "SO-100",
    "customer_id": "C1",
    "sku": "SKU-A",
    "qty": "2",
    "unit_price": "$129.00",
    "region": "EMEA",
    "channel": "web",
    "ts": "06/12/2026",
}

_OPS_RAW = {
    "service": "checkout-api",
    "region": "EMEA",
    "level": "error",
    "status_code": "503",
    "latency_ms": "1400.0",
    "message": "upstream timeout",
    "ts": "2026-06-12T10:00:00Z",
}


async def _collect(stream, n: int, timeout: float = 2.0) -> list:
    """Pull ``n`` messages off an in-proc subscription with a safety timeout."""

    out = []
    for _ in range(n):
        msg = await asyncio.wait_for(stream.__anext__(), timeout=timeout)
        out.append(msg)
    return out


@pytest.mark.asyncio
async def test_sales_record_published_and_audited(sink, source, publisher, idem):
    raw_stream = source.subscribe([topics.RAW_SALES], group="raw-consumer")
    audit_stream = source.subscribe([topics.AUDIT], group="audit-consumer")

    res = await ingest_record(
        "sales",
        _SALES_RAW,
        tenant_id="acme",
        source_system="simulator",
        ctx_sink=publisher,
        idem=idem,
        writer=None,
    )
    assert res.outcome is IngestOutcome.LANDED
    assert res.published is True

    # 1. the envelope reached edis.raw.sales.v1, keyed by tenant_id.
    raw_msg = (await _collect(raw_stream, 1))[0]
    assert raw_msg.topic == topics.RAW_SALES
    assert raw_msg.key == "acme"
    env = parse_message(raw_msg)
    assert isinstance(env, IngestEnvelope)
    assert env.domain == "sales"
    assert env.tenant_id == "acme"
    assert env.idempotency_key == "sales:acme:simulator:SO-100"
    assert env.payload["unit_price"] == 129.0  # coerced before publish

    # 2. an AuditEvent (DATA_WRITE / ALLOW) for the same write reached the audit topic.
    audit_msg = (await _collect(audit_stream, 1))[0]
    assert audit_msg.topic == topics.AUDIT
    assert audit_msg.key == "acme"
    audit = parse_message(audit_msg)
    assert isinstance(audit, AuditEvent)
    assert audit.action == "DATA_WRITE"
    assert audit.outcome == "ALLOW"
    assert audit.tenant_id == "acme"
    assert audit.resource["type"] == "raw_event"
    assert audit.resource["domain"] == "sales"
    # the audit references the published envelope's event id.
    assert audit.resource["id"] == str(env.event_id)


@pytest.mark.asyncio
async def test_ops_record_keyed_by_tenant_and_service(sink, source, publisher, idem):
    raw_stream = source.subscribe([topics.RAW_OPS], group="ops-consumer")

    res = await ingest_record(
        "ops",
        _OPS_RAW,
        tenant_id="acme",
        source_system="erp",
        ctx_sink=publisher,
        idem=idem,
        writer=None,
    )
    assert res.outcome is IngestOutcome.LANDED

    msg = (await _collect(raw_stream, 1))[0]
    assert msg.topic == topics.RAW_OPS
    assert msg.key == "acme|checkout-api"  # arch §4.3 ops keying
    env = parse_message(msg)
    assert env.domain == "ops"
    assert env.payload["latency_ms"] == 1400.0


@pytest.mark.asyncio
async def test_duplicate_does_not_republish(sink, source, publisher, idem):
    raw_stream = source.subscribe([topics.RAW_SALES], group="dup-consumer")

    common = dict(
        tenant_id="acme",
        source_system="simulator",
        ctx_sink=publisher,
        idem=idem,
        writer=None,
    )
    first = await ingest_record("sales", dict(_SALES_RAW), **common)
    second = await ingest_record("sales", dict(_SALES_RAW), **common)
    assert first.outcome is IngestOutcome.LANDED
    assert second.outcome is IngestOutcome.DUPLICATE

    # exactly one raw message was published; a second pull times out.
    msgs = await _collect(raw_stream, 1)
    assert len(msgs) == 1
    with pytest.raises(asyncio.TimeoutError):
        await _collect(raw_stream, 1, timeout=0.3)


@pytest.mark.asyncio
async def test_anomaly_label_present_on_published_envelope(sink, source, publisher, idem):
    raw_stream = source.subscribe([topics.RAW_OPS], group="label-consumer")

    await ingest_record(
        "ops",
        dict(_OPS_RAW),
        tenant_id="acme",
        source_system="simulator",
        ctx_sink=publisher,
        idem=idem,
        writer=None,
        anomaly_label="outage",
    )
    env = parse_message((await _collect(raw_stream, 1))[0])
    assert env.anomaly_label == "outage"
