"""Event-bus ports: the stable seam every EDIS service publishes/consumes through.

This module defines the two abstract ports -- :class:`EventSink` (produce) and
:class:`MessageSource` (consume) -- plus the wire :class:`Message`, a generic
:func:`parse_message` that deserializes any topic to its canonical Pydantic model
via :data:`edis_contracts.topics.TOPIC_MODEL`, and the :func:`make_sink` /
:func:`make_source` factories that select a concrete backend from
``settings.sink_backend``. Downstream code is identical regardless of whether the
backend is Kafka (Redpanda), Redis Streams, or the in-process queue used in tests.

Nothing here connects to a live broker at import time; connections happen lazily
inside :meth:`EventSink.start` / :meth:`MessageSource.start`.
"""

from __future__ import annotations

import abc
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel

from edis_contracts.topics import TOPIC_MODEL

if TYPE_CHECKING:
    from edis_platform.settings import Settings


@dataclass
class Message:
    """One bus record as it travels the wire.

    ``value`` is always a plain JSON-decoded ``dict`` (the deserialized event
    body); use :func:`parse_message` to turn it into the canonical Pydantic
    model for ``topic``.
    """

    topic: str
    key: str | None
    value: dict
    headers: dict = field(default_factory=dict)


def _serialize(value: BaseModel | dict) -> bytes:
    """Serialize a pydantic model or plain dict to compact UTF-8 JSON bytes."""

    if isinstance(value, BaseModel):
        return value.model_dump_json().encode("utf-8")
    return json.dumps(value, default=str, separators=(",", ":")).encode("utf-8")


def parse_message(msg: Message) -> BaseModel | dict:
    """Deserialize ``msg.value`` to its canonical model, falling back to a dict.

    Topics carrying a typed payload (every entry in
    :data:`~edis_contracts.topics.TOPIC_MODEL`) are validated into their Pydantic
    model. Unknown topics (e.g. DLQ envelopes) are returned as the raw ``dict``.
    """

    model = TOPIC_MODEL.get(msg.topic)
    if model is None:
        return msg.value
    return model.model_validate(msg.value)


class EventSink(abc.ABC):
    """Produce-side port: publish typed events to a topic.

    Lifecycle is explicit -- :meth:`start` before first publish, :meth:`stop` at
    shutdown -- and the type is an async context manager for convenience.
    """

    @abc.abstractmethod
    async def start(self) -> None:
        """Open the underlying producer/connection (lazy; never at import)."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Flush and close the underlying producer/connection."""

    @abc.abstractmethod
    async def publish(self, topic: str, key: str | None, value: BaseModel | dict) -> None:
        """Publish ``value`` (a pydantic model or dict) to ``topic`` under ``key``."""

    async def __aenter__(self) -> "EventSink":
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()


class MessageSource(abc.ABC):
    """Consume-side port: subscribe to topics and iterate :class:`Message`s.

    ``subscribe`` joins a consumer ``group`` (so multiple service replicas share
    the partitions) and yields decoded messages until :meth:`stop` is called.
    """

    @abc.abstractmethod
    async def start(self) -> None:
        """Open the underlying consumer/connection (lazy; never at import)."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Close the underlying consumer/connection."""

    @abc.abstractmethod
    def subscribe(self, topics: list[str], group: str) -> AsyncIterator[Message]:
        """Return an async iterator of messages from ``topics`` for ``group``."""

    async def __aenter__(self) -> "MessageSource":
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()


def make_sink(settings: "Settings") -> EventSink:
    """Construct the :class:`EventSink` selected by ``settings.sink_backend``."""

    backend = settings.sink_backend
    if backend == "kafka":
        from edis_platform.bus.kafka import KafkaEventSink

        return KafkaEventSink(settings)
    if backend == "redis":
        from edis_platform.bus.redis_streams import RedisStreamsEventSink

        return RedisStreamsEventSink(settings)
    if backend == "inproc":
        from edis_platform.bus.inproc import InProcEventSink

        return InProcEventSink(settings)
    raise ValueError(f"unknown sink_backend: {backend!r}")


def make_source(settings: "Settings") -> MessageSource:
    """Construct the :class:`MessageSource` selected by ``settings.sink_backend``."""

    backend = settings.sink_backend
    if backend == "kafka":
        from edis_platform.bus.kafka import KafkaMessageSource

        return KafkaMessageSource(settings)
    if backend == "redis":
        from edis_platform.bus.redis_streams import RedisStreamsMessageSource

        return RedisStreamsMessageSource(settings)
    if backend == "inproc":
        from edis_platform.bus.inproc import InProcMessageSource

        return InProcMessageSource(settings)
    raise ValueError(f"unknown sink_backend: {backend!r}")
