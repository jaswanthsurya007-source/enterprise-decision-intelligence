"""In-process bus round-trip: publish a MetricPoint, consume it back.

Pure-python (no Docker, no broker) so it runs everywhere in CI. Exercises the
real ``inproc`` pub/sub path through the public ports -- ``make_sink`` /
``make_source`` / ``parse_message`` -- exactly as a service would use them.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from edis_contracts import topics
from edis_contracts.events import MetricPoint
from edis_platform.bus import make_sink, make_source, parse_message
from edis_platform.bus.inproc import reset_brokers
from edis_platform.settings import Settings


@pytest.fixture(autouse=True)
def _isolated_broker():
    """Each test gets a clean in-process broker registry."""

    reset_brokers()
    yield
    reset_brokers()


def _settings() -> Settings:
    # Distinct Settings per call would isolate brokers; build once and share it
    # between sink and source so they resolve to the SAME in-process broker.
    return Settings(sink_backend="inproc")


async def test_metric_point_round_trip() -> None:
    settings = _settings()
    sink = make_sink(settings)
    source = make_source(settings)
    await sink.start()
    await source.start()

    point = MetricPoint(
        tenant_id="tenant-a",
        metric_key="revenue",
        ts=datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc),
        value=12345.67,
        dimensions={"region": "EMEA", "channel": "web"},
        unit="USD",
        source="integration",
    )

    stream = source.subscribe([topics.METRICS_POINTS], group="test-group")

    # Subscribe (registers the group queue) before publishing so the message is
    # delivered. anext is awaited with a timeout to fail fast if delivery breaks.
    async def _consume_one():
        return await anext(stream)

    consumer = asyncio.create_task(_consume_one())
    await asyncio.sleep(0)  # let the consumer register its group queue

    await sink.publish(topics.METRICS_POINTS, key="tenant-a:revenue", value=point)

    msg = await asyncio.wait_for(consumer, timeout=2.0)

    # Wire-level assertions.
    assert msg.topic == topics.METRICS_POINTS
    assert msg.key == "tenant-a:revenue"
    assert isinstance(msg.value, dict)
    assert msg.value["metric_key"] == "revenue"

    # parse_message rehydrates the canonical contract model.
    parsed = parse_message(msg)
    assert isinstance(parsed, MetricPoint)
    assert parsed == point
    assert parsed.tenant_id == "tenant-a"
    assert parsed.value == pytest.approx(12345.67)
    assert parsed.dimensions == {"region": "EMEA", "channel": "web"}

    await stream.aclose()
    await source.stop()
    await sink.stop()


async def test_two_groups_each_receive_the_message() -> None:
    """Fan-out: distinct consumer groups each get their own copy."""

    settings = _settings()
    sink = make_sink(settings)
    source_a = make_source(settings)
    source_b = make_source(settings)
    await sink.start()
    await source_a.start()
    await source_b.start()

    point = MetricPoint(
        tenant_id="tenant-a",
        metric_key="orders",
        ts=datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc),
        value=10.0,
        source="integration",
    )

    stream_a = source_a.subscribe([topics.METRICS_POINTS], group="group-a")
    stream_b = source_b.subscribe([topics.METRICS_POINTS], group="group-b")

    task_a = asyncio.create_task(anext(stream_a))
    task_b = asyncio.create_task(anext(stream_b))
    await asyncio.sleep(0)

    await sink.publish(topics.METRICS_POINTS, key="tenant-a:orders", value=point)

    msg_a = await asyncio.wait_for(task_a, timeout=2.0)
    msg_b = await asyncio.wait_for(task_b, timeout=2.0)

    assert parse_message(msg_a) == point
    assert parse_message(msg_b) == point

    await stream_a.aclose()
    await stream_b.aclose()
    await source_a.stop()
    await source_b.stop()
    await sink.stop()
