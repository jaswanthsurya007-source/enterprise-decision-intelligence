"""Unit tests for the SSE bridge core over the in-process bus (no broker)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from edis_contracts import topics
from edis_contracts.events import MetricPoint
from edis_contracts.findings import Finding, FindingKind
from edis_platform.bus.base import make_sink, make_source
from edis_platform.settings import Settings

from edis_gateway.sse.bridge import (
    ANOMALY_EVENT,
    Concern,
    bridge_stream,
    format_sse,
)


def test_format_sse_single_line():
    frame = format_sse('{"a":1}', event="metric", event_id="x").decode()
    assert "event: metric\n" in frame
    assert "id: x\n" in frame
    assert 'data: {"a":1}\n' in frame
    assert frame.endswith("\n\n")


def test_format_sse_multiline_payload_stays_one_event():
    frame = format_sse("line1\nline2", event="metric").decode()
    assert frame.count("data: ") == 2
    assert frame.count("\n\n") == 1  # one terminating blank line => one SSE event


def _metric(tenant: str) -> MetricPoint:
    return MetricPoint(
        tenant_id=tenant,
        metric_key="revenue",
        ts=datetime.now(timezone.utc),
        value=42.0,
        dimensions={"region": "EMEA"},
        source="test",
    )


def _finding(tenant: str) -> Finding:
    return Finding(
        finding_id=uuid4(),
        tenant_id=tenant,
        kind=FindingKind.LEVEL_SHIFT,
        metric_key="revenue",
        window_start=datetime.now(timezone.utc),
        window_end=datetime.now(timezone.utc),
        detector="stl",
        detector_version="1.0",
        observed_value=61000.0,
        expected_value=95000.0,
        deviation=-34000.0,
        deviation_pct=-35.8,
        score=5.8,
        severity=0.8,
        confidence=0.9,
        business_impact_input=0.7,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_bridge_pushes_only_tenant_matching_events():
    settings = Settings(sink_backend="inproc")
    sink = make_sink(settings)
    source = make_source(settings)
    await sink.start()

    concern = Concern(topics.METRICS_POINTS, "metric", id_field=None)
    gen = bridge_stream(
        source=source,
        concern=concern,
        tenant_id="acme",
        group="g-test",
        heartbeat_seconds=5.0,
    )

    # Drain the opening ": connected" frame.
    opening = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert opening == b": connected\n\n"

    # Publish a cross-tenant event (must be dropped) then a matching one.
    await sink.publish(topics.METRICS_POINTS, key="globex:revenue", value=_metric("globex"))
    await sink.publish(topics.METRICS_POINTS, key="acme:revenue", value=_metric("acme"))

    frame = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
    text = frame.decode()
    assert "event: metric" in text
    assert '"tenant_id":"acme"' in text
    assert "globex" not in text  # the other tenant's event never reaches the browser

    await gen.aclose()
    await sink.stop()


@pytest.mark.asyncio
async def test_bridge_emits_heartbeat_when_idle():
    settings = Settings(sink_backend="inproc")
    source = make_source(settings)
    concern = Concern(topics.FINDINGS, ANOMALY_EVENT, id_field="finding_id")
    gen = bridge_stream(
        source=source,
        concern=concern,
        tenant_id="acme",
        group="g-hb",
        heartbeat_seconds=0.05,  # fast heartbeat for the test
    )
    await asyncio.wait_for(gen.__anext__(), timeout=1.0)  # ": connected"
    hb = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert hb == b": hb\n\n"
    await gen.aclose()


@pytest.mark.asyncio
async def test_bridge_sets_event_id_from_payload():
    settings = Settings(sink_backend="inproc")
    sink = make_sink(settings)
    source = make_source(settings)
    await sink.start()

    concern = Concern(topics.FINDINGS, ANOMALY_EVENT, id_field="finding_id")
    gen = bridge_stream(
        source=source, concern=concern, tenant_id="acme", group="g-id", heartbeat_seconds=5.0
    )
    await asyncio.wait_for(gen.__anext__(), timeout=1.0)  # ": connected"

    finding = _finding("acme")
    await sink.publish(topics.FINDINGS, key="acme:f", value=finding)
    frame = (await asyncio.wait_for(gen.__anext__(), timeout=2.0)).decode()
    assert f"id: {finding.finding_id}" in frame
    assert "event: anomaly" in frame

    await gen.aclose()
    await sink.stop()


@pytest.mark.asyncio
async def test_bridge_stops_when_disconnected():
    settings = Settings(sink_backend="inproc")
    source = make_source(settings)
    concern = Concern(topics.METRICS_POINTS, "metric", id_field=None)

    async def disconnected() -> bool:
        return True  # client already gone

    gen = bridge_stream(
        source=source,
        concern=concern,
        tenant_id="acme",
        group="g-disc",
        heartbeat_seconds=5.0,
        is_disconnected=disconnected,
    )
    frames = [f async for f in gen]
    # Only the opening frame, then a clean stop (no hang, no extra frames).
    assert frames == [b": connected\n\n"]
