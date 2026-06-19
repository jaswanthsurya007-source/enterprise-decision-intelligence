"""Event-bus abstraction -- one ``EventSink`` / ``MessageSource`` port, three backends.

Every EDIS layer produces and consumes events exclusively through these ports, so
the concrete transport (Kafka/Redpanda, Redis Streams, or the in-process queue
used on a laptop and in tests) is a single config switch -- ``EDIS_SINK_BACKEND``
-- with zero downstream change. :func:`make_sink` / :func:`make_source` select the
backend from :class:`~edis_platform.settings.Settings`; :func:`parse_message`
deserializes any :class:`Message` to its canonical contract model.

Example::

    from edis_platform.settings import get_settings
    from edis_platform.bus import make_sink, make_source, parse_message

    settings = get_settings()          # sink_backend defaults to "inproc"
    async with make_sink(settings) as sink:
        await sink.publish("edis.metrics.points.v1", key="t1:revenue", value=point)

    async with make_source(settings) as source:
        async for msg in source.subscribe(["edis.metrics.points.v1"], group="g1"):
            event = parse_message(msg)  # -> MetricPoint
"""

from __future__ import annotations

from edis_platform.bus.base import (
    EventSink,
    Message,
    MessageSource,
    make_sink,
    make_source,
    parse_message,
)

__all__ = [
    "Message",
    "EventSink",
    "MessageSource",
    "parse_message",
    "make_sink",
    "make_source",
]
