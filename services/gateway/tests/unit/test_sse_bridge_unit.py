"""P3 — the Kafka->browser SSE bridge over the in-process bus (no broker, no keys).

Publishing a payload to a bridged topic makes the bridge yield exactly one SSE-framed
event for the connection's tenant; a cross-tenant payload on the same shared topic is
dropped (the isolation boundary). When idle the bridge emits a heartbeat comment so the
connection stays alive. All driven by ``Settings(sink_backend="inproc")`` — no Redpanda.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from edis_contracts import topics
from edis_contracts.events import MetricPoint
from edis_platform.bus.base import make_sink, make_source
from edis_platform.settings import Settings

from edis_gateway.sse.bridge import Concern, METRICS_EVENT, bridge_stream, format_sse


def _metric(tenant: str, value: float = 42.0) -> MetricPoint:
    return MetricPoint(
        tenant_id=tenant,
        metric_key="revenue",
        ts=datetime.now(timezone.utc),
        value=value,
        dimensions={"region": "EMEA"},
        source="test",
    )


def test_format_sse_multiline_payload_is_one_event():
    """A JSON body with an embedded newline stays a single SSE event (one blank-line end)."""

    frame = format_sse("line1\nline2", event=METRICS_EVENT).decode()
    assert frame.startswith("event: metric\n")
    assert frame.count("data: ") == 2
    assert frame.count("\n\n") == 1


@pytest.mark.asyncio
async def test_publish_yields_one_frame_for_matching_tenant():
    """Publish to the bridged topic -> the stream yields the matching-tenant SSE frame."""

    settings = Settings(sink_backend="inproc")
    sink, source = make_sink(settings), make_source(settings)
    await sink.start()
    concern = Concern(topics.METRICS_POINTS, METRICS_EVENT, id_field=None)
    gen = bridge_stream(
        source=source, concern=concern, tenant_id="acme", group="g-pub", heartbeat_seconds=5.0
    )
    try:
        assert await asyncio.wait_for(gen.__anext__(), timeout=1.0) == b": connected\n\n"
        # A cross-tenant event first (must be dropped), then the matching one.
        await sink.publish(
            topics.METRICS_POINTS, key="globex:revenue", value=_metric("globex", 9.0)
        )
        await sink.publish(topics.METRICS_POINTS, key="acme:revenue", value=_metric("acme", 42.0))

        frame = (await asyncio.wait_for(gen.__anext__(), timeout=2.0)).decode()
        assert "event: metric" in frame
        assert '"tenant_id":"acme"' in frame
        assert "globex" not in frame  # the other tenant's event never reaches the browser
    finally:
        await gen.aclose()
        await sink.stop()


@pytest.mark.asyncio
async def test_idle_stream_emits_heartbeat():
    """With no event flowing, the bridge emits a heartbeat comment within the interval."""

    settings = Settings(sink_backend="inproc")
    source = make_source(settings)
    concern = Concern(topics.METRICS_POINTS, METRICS_EVENT, id_field=None)
    gen = bridge_stream(
        source=source, concern=concern, tenant_id="acme", group="g-hb", heartbeat_seconds=0.05
    )
    try:
        assert await asyncio.wait_for(gen.__anext__(), timeout=1.0) == b": connected\n\n"
        hb = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert hb == b": hb\n\n"
    finally:
        await gen.aclose()


@pytest.mark.asyncio
async def test_disconnect_stops_stream_cleanly():
    """If the client is already gone, the bridge yields only the opening frame, no hang."""

    settings = Settings(sink_backend="inproc")
    source = make_source(settings)
    concern = Concern(topics.METRICS_POINTS, METRICS_EVENT, id_field=None)

    async def gone() -> bool:
        return True

    gen = bridge_stream(
        source=source,
        concern=concern,
        tenant_id="acme",
        group="g-disc",
        heartbeat_seconds=5.0,
        is_disconnected=gone,
    )
    frames = [f async for f in gen]
    assert frames == [b": connected\n\n"]
