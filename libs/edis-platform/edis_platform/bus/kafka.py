"""Kafka (Redpanda) bus backend built on ``aiokafka``.

The canonical production backend: real partitioning, replay, and consumer
groups over the Kafka API (served by Redpanda). ``aiokafka`` is imported lazily
inside :meth:`start` so importing this module -- and any service that wires the
bus at import time -- never requires a running broker. Keys partition by
``tenant_id`` (or ``tenant:entity``) so per-tenant ordering is preserved on a
single partition.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from edis_platform.bus.base import EventSink, Message, MessageSource, _serialize
from edis_platform.logging import get_logger

if TYPE_CHECKING:
    from edis_platform.settings import Settings

_log = get_logger(__name__)


class KafkaEventSink(EventSink):
    """:class:`EventSink` over an ``aiokafka`` producer -> Redpanda."""

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings
        self._producer: Any | None = None

    async def start(self) -> None:
        if self._producer is not None:
            return
        from aiokafka import AIOKafkaProducer  # lazy: no broker needed to import

        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._settings.kafka_bootstrap_servers,
            enable_idempotence=True,
            acks="all",
        )
        await self._producer.start()
        _log.info("kafka sink started", extra={"bus_backend": "kafka"})

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish(self, topic: str, key: str | None, value: BaseModel | dict) -> None:
        if self._producer is None:
            raise RuntimeError("KafkaEventSink.publish called before start()")
        await self._producer.send_and_wait(
            topic,
            value=_serialize(value),
            key=key.encode("utf-8") if key is not None else None,
        )


class KafkaMessageSource(MessageSource):
    """:class:`MessageSource` over an ``aiokafka`` consumer group."""

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings
        self._consumer: Any | None = None
        self._started = False

    async def start(self) -> None:
        # The consumer is constructed in subscribe() once the topics + group are
        # known; start() just records intent so callers can manage lifecycle.
        self._started = True

    async def stop(self) -> None:
        self._started = False
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None

    async def subscribe(self, topics: list[str], group: str) -> AsyncIterator[Message]:
        import json

        from aiokafka import AIOKafkaConsumer  # lazy import

        self._consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=self._settings.kafka_bootstrap_servers,
            group_id=group,
            enable_auto_commit=True,
            auto_offset_reset="earliest",
        )
        await self._consumer.start()
        _log.info(
            "kafka source subscribed",
            extra={"bus_backend": "kafka", "topics": topics, "group": group},
        )
        try:
            async for record in self._consumer:
                value = json.loads(record.value.decode("utf-8")) if record.value else {}
                key = record.key.decode("utf-8") if record.key else None
                headers = {
                    k: (v.decode("utf-8") if isinstance(v, bytes) else v)
                    for k, v in (record.headers or [])
                }
                yield Message(topic=record.topic, key=key, value=value, headers=headers)
        finally:
            await self._consumer.stop()
            self._consumer = None
