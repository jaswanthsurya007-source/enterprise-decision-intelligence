"""Redis Streams bus backend -- the laptop fallback behind the same ports.

Publishes with ``XADD`` (one stream per topic) and consumes with consumer-group
reads (``XGROUP CREATE`` + ``XREADGROUP``), giving the same at-least-once,
load-balanced consumer-group semantics as Kafka without Redpanda. The ``redis``
client is imported lazily inside :meth:`start` so importing this module never
requires a running Redis. The event body is stored under the ``data`` field of
each stream entry as JSON; the key (partition hint) is stored under ``key``.
"""

from __future__ import annotations

import json
import socket
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from edis_platform.bus.base import EventSink, Message, MessageSource, _serialize
from edis_platform.logging import get_logger

if TYPE_CHECKING:
    from edis_platform.settings import Settings

_log = get_logger(__name__)


class RedisStreamsEventSink(EventSink):
    """:class:`EventSink` over Redis ``XADD``."""

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings
        self._client: Any | None = None

    async def start(self) -> None:
        if self._client is not None:
            return
        from redis import asyncio as aioredis  # lazy: no server needed to import

        self._client = aioredis.from_url(self._settings.redis_url, decode_responses=True)
        _log.info("redis sink started", extra={"bus_backend": "redis"})

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def publish(self, topic: str, key: str | None, value: BaseModel | dict) -> None:
        if self._client is None:
            raise RuntimeError("RedisStreamsEventSink.publish called before start()")
        fields = {
            "topic": topic,
            "key": key if key is not None else "",
            "data": _serialize(value).decode("utf-8"),
        }
        await self._client.xadd(topic, fields)


class RedisStreamsMessageSource(MessageSource):
    """:class:`MessageSource` over Redis consumer-group stream reads."""

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings
        self._client: Any | None = None
        self._stopped = False
        # Unique, stable per-process consumer name within the group.
        self._consumer_name = f"{socket.gethostname()}-{id(self)}"

    async def start(self) -> None:
        if self._client is not None:
            return
        from redis import asyncio as aioredis  # lazy import

        self._client = aioredis.from_url(self._settings.redis_url, decode_responses=True)
        self._stopped = False

    async def stop(self) -> None:
        self._stopped = True
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def subscribe(self, topics: list[str], group: str) -> AsyncIterator[Message]:
        if self._client is None:
            await self.start()
        client = self._client
        assert client is not None

        # Create the group at the stream tail for each topic (idempotent).
        for topic in topics:
            try:
                await client.xgroup_create(name=topic, groupname=group, id="$", mkstream=True)
            except Exception as exc:  # BUSYGROUP -> group already exists
                if "BUSYGROUP" not in str(exc):
                    raise

        streams = {topic: ">" for topic in topics}
        _log.info(
            "redis source subscribed",
            extra={"bus_backend": "redis", "topics": topics, "group": group},
        )
        while not self._stopped:
            response = await client.xreadgroup(
                groupname=group,
                consumername=self._consumer_name,
                streams=streams,
                count=100,
                block=1000,  # ms; loop wakes to honour stop()
            )
            if not response:
                continue
            for topic, entries in response:
                for entry_id, fields in entries:
                    raw = fields.get("data", "{}")
                    value = json.loads(raw) if raw else {}
                    key = fields.get("key") or None
                    await client.xack(topic, group, entry_id)
                    yield Message(topic=topic, key=key, value=value, headers={})
