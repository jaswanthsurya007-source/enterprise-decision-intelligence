"""IngestEnvelope construction tests — the stable L1 -> L2 boundary.

After coercion/validation/keying, :func:`ingestion.pipeline.envelope_builder.build_envelope`
stamps the server-side provenance/observability fields and freezes the result.
These tests assert the envelope is built correctly from a validated payload:

* required identity/provenance fields (``domain``, ``tenant_id``, ``source_system``,
  ``schema_ref``, ``idempotency_key``) are set;
* ``event_ts`` is lifted from the payload as a tz-aware UTC ``datetime`` (sales
  uses ``order_ts``; ops/customer use ``event_ts``);
* ``ingest_ts`` is freshly stamped (>= event_ts in our fixtures) and tz-aware;
* ``event_id`` is a fresh UUID and the envelope is frozen (immutable);
* ``anomaly_label`` ground truth propagates through unchanged;
* ``payload`` is a JSON-safe dict mirroring the validated model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
from edis_contracts.ingest import IngestEnvelope, OpsPayloadV1, SalesPayloadV1
from pydantic import ValidationError

from ingestion.pipeline.engine import IngestOutcome, ingest_record
from ingestion.pipeline.envelope_builder import build_envelope

_ORDER_TS = datetime(2026, 6, 12, 9, 30, tzinfo=timezone.utc)
_EVENT_TS = datetime(2026, 6, 12, 9, 31, tzinfo=timezone.utc)


def _sales_model() -> SalesPayloadV1:
    return SalesPayloadV1(
        order_id="SO-1",
        customer_id="C1",
        sku="SKU-A",
        qty=2,
        unit_price=129.0,
        currency="USD",
        region="EMEA",
        channel="web",
        order_ts=_ORDER_TS,
    )


def test_build_sales_envelope_fields():
    env = build_envelope(
        "sales",
        _sales_model(),
        tenant_id="acme",
        source_system="simulator",
        idempotency_key="sales:acme:simulator:SO-1",
    )
    assert isinstance(env, IngestEnvelope)
    assert env.domain == "sales"
    assert env.tenant_id == "acme"
    assert env.source_system == "simulator"
    assert env.schema_ref == "sales.v1"
    assert env.idempotency_key == "sales:acme:simulator:SO-1"
    assert env.producer == "ingestion"
    # event_ts lifted from payload order_ts, tz-aware UTC.
    assert env.event_ts == _ORDER_TS
    assert env.event_ts.tzinfo is not None
    # ingest_ts freshly stamped, tz-aware UTC.
    assert env.ingest_ts.tzinfo is not None
    assert env.ingest_ts >= env.event_ts
    # event_id is a fresh UUID.
    assert isinstance(env.event_id, UUID)
    # payload is a JSON-safe dict mirroring the validated model.
    assert env.payload["order_id"] == "SO-1"
    assert env.payload["unit_price"] == 129.0


def test_ops_envelope_uses_event_ts():
    model = OpsPayloadV1(
        service="checkout-api",
        region="EMEA",
        level="error",
        status_code=503,
        latency_ms=1400.0,
        message="upstream timeout",
        event_ts=_EVENT_TS,
    )
    env = build_envelope(
        "ops",
        model,
        tenant_id="acme",
        source_system="erp",
        idempotency_key="abc123",
    )
    assert env.domain == "ops"
    assert env.schema_ref == "ops.v1"
    assert env.event_ts == _EVENT_TS
    assert env.payload["service"] == "checkout-api"


def test_anomaly_label_propagates():
    env = build_envelope(
        "sales",
        _sales_model(),
        tenant_id="acme",
        source_system="simulator",
        idempotency_key="k",
        anomaly_label="outage",
    )
    assert env.anomaly_label == "outage"


def test_anomaly_label_defaults_none():
    env = build_envelope(
        "sales",
        _sales_model(),
        tenant_id="acme",
        source_system="simulator",
        idempotency_key="k",
    )
    assert env.anomaly_label is None


def test_trace_context_carried():
    env = build_envelope(
        "sales",
        _sales_model(),
        tenant_id="acme",
        source_system="simulator",
        idempotency_key="k",
        trace_context={"traceparent": "00-abc-def-01"},
    )
    assert env.trace_context == {"traceparent": "00-abc-def-01"}


def test_envelope_is_frozen():
    env = build_envelope(
        "sales",
        _sales_model(),
        tenant_id="acme",
        source_system="simulator",
        idempotency_key="k",
    )
    with pytest.raises(ValidationError):
        env.tenant_id = "evil"  # type: ignore[misc]


def test_each_envelope_gets_unique_event_id():
    a = build_envelope(
        "sales", _sales_model(), tenant_id="t", source_system="s", idempotency_key="k"
    )
    b = build_envelope(
        "sales", _sales_model(), tenant_id="t", source_system="s", idempotency_key="k"
    )
    assert a.event_id != b.event_id


# --- propagation through the full engine path --------------------------------


@pytest.mark.asyncio
async def test_engine_builds_envelope_with_label(publisher, idem):
    res = await ingest_record(
        "sales",
        {
            "order_id": "SO-7",
            "customer_id": "C7",
            "sku": "SKU-A",
            "qty": "1",
            "unit_price": "$95.00",
            "region": "EMEA",
            "channel": "web",
            "ts": "06/12/2026",
        },
        tenant_id="acme",
        source_system="simulator",
        ctx_sink=publisher,
        idem=idem,
        writer=None,
        anomaly_label="outage",
        trace_context={"traceparent": "00-trace-span-01"},
    )
    assert res.outcome is IngestOutcome.LANDED
    env = res.envelope
    assert env is not None
    assert env.anomaly_label == "outage"  # ground truth propagated
    assert env.idempotency_key == "sales:acme:simulator:SO-7"
    assert env.tenant_id == "acme"
    assert env.event_ts == datetime(2026, 6, 12, tzinfo=timezone.utc)
    assert env.trace_context == {"traceparent": "00-trace-span-01"}
    # coercion happened before the envelope: price is a float in the payload.
    assert env.payload["unit_price"] == 95.0
