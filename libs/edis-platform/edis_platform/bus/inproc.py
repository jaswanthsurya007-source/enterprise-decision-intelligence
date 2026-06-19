"""In-process event bus -- real pub/sub over ``asyncio`` queues, no broker.

This backend implements genuine in-process publish/subscribe so the whole system
(and its tests) can run on a laptop with no Redpanda or Redis. A process-global
broker registry holds, per ``(topic, group)``, one fan-in queue: every publish to
a topic is copied into the queue of *each* group subscribed to it, while
consumers within the *same* group share one queue (consumer-group load
balancing, matching Kafka/Redis-Streams semantics). Ordering per topic is
preserved because a single queue is appended to in publish order.

The registry is keyed by ``id(settings)`` so independent ``Settings`` instances
(e.g. separate tests) get isolated brokers, while a shared settings object lets a
publisher and a consumer in the same process find each other.

``subscribe`` registers the group's queues **eagerly, at call time** (it is a
plain method that returns an async iterator, per the :class:`MessageSource`
contract -- not an ``async def`` generator whose body would run lazily on the
first ``__anext__``). This means the natural ``subscribe -> publish -> consume``
pattern delivers the message: any publish *after* the ``subscribe`` call lands in
the already-registered queue. (A publish *before* ``subscribe`` is at-most-once
and not delivered, exactly like joining a live topic.)

Example::

    from edis_platform.settings import Settings
    from edis_platform.bus import make_sink, make_source, parse_message
    from edis_contracts.events import MetricPoint
    from edis_contracts import topics

    settings = Settings(sink_backend="inproc")
    sink, source = make_sink(settings), make_source(settings)
    await sink.start()
    await source.start()
    stream = source.subscribe([topics.METRICS_POINTS], group="demo")  # queue registered now
    await sink.publish(topics.METRICS_POINTS, key="t1:revenue",
                       value=MetricPoint(tenant_id="t1", metric_key="revenue",
                                         ts=..., value=42.0, source="demo"))
    msg = await anext(stream)
    point = parse_message(msg)   # -> MetricPoint
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from pydantic import BaseModel

from edis_platform.bus.base import EventSink, Message, MessageSource, _serialize

if TYPE_CHECKING:
    from edis_platform.settings import Settings


class _Broker:
    """Per-settings in-process broker: topic -> {group -> shared queue}.

    Queue registration is synchronous so ``subscribe`` can register a group's
    queue the instant it is called (before any publish). This is safe because the
    asyncio event loop is single-threaded: the ``dict`` mutations below never
    interleave with an ``await``.
    """

    def __init__(self) -> None:
        # topic -> group -> queue shared by all consumers in that group
        self._queues: dict[str, dict[str, asyncio.Queue[Message]]] = {}

    def get_group_queue(self, topic: str, group: str) -> asyncio.Queue[Message]:
        """Return (creating if needed) the shared queue for ``(topic, group)``."""

        groups = self._queues.setdefault(topic, {})
        queue = groups.get(group)
        if queue is None:
            queue = asyncio.Queue()
            groups[group] = queue
        return queue

    def publish(self, message: Message) -> None:
        """Fan the message out to one queue per subscribed group."""

        for queue in list(self._queues.get(message.topic, {}).values()):
            queue.put_nowait(message)


# Process-global registry keyed by id(settings) so distinct Settings -> distinct
# brokers (test isolation), shared Settings -> shared broker (real pub/sub).
_BROKERS: dict[int, _Broker] = {}


def _broker_for(settings: "Settings") -> _Broker:
    key = id(settings)
    broker = _BROKERS.get(key)
    if broker is None:
        broker = _Broker()
        _BROKERS[key] = broker
    return broker


def reset_brokers() -> None:
    """Drop all in-process brokers (test hook to isolate runs)."""

    _BROKERS.clear()


class InProcEventSink(EventSink):
    """:class:`EventSink` over the in-process broker."""

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings
        self._broker = _broker_for(settings)
        self._started = False

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    async def publish(self, topic: str, key: str | None, value: BaseModel | dict) -> None:
        # Round-trip through JSON so consumers always receive a plain dict,
        # exactly as the Kafka/Redis backends deliver it.
        decoded = json.loads(_serialize(value).decode("utf-8"))
        self._broker.publish(Message(topic=topic, key=key, value=decoded, headers={}))


class InProcMessageSource(MessageSource):
    """:class:`MessageSource` over the in-process broker."""

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings
        self._broker = _broker_for(settings)
        self._started = False
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        self._started = True
        self._stopped.clear()

    async def stop(self) -> None:
        self._started = False
        self._stopped.set()

    def subscribe(self, topics: list[str], group: str) -> AsyncIterator[Message]:
        """Return an async iterator of messages from ``topics`` for ``group``.

        The group's queues are registered **now** (synchronously, before this
        method returns), so a publish that happens after this call -- but before
        the first ``__anext__`` -- is still delivered. The returned async
        generator multiplexes the subscribed topic queues, keeping one in-flight
        ``get`` per queue so no dequeued message is lost to cancellation between
        iterations, and exits cleanly on :meth:`stop` or cancellation.
        """

        queues = [self._broker.get_group_queue(t, group) for t in topics]
        stopped = self._stopped

        async def _iter() -> AsyncIterator[Message]:
            stop_task = asyncio.create_task(stopped.wait())
            # One in-flight get() per queue, recreated only after it resolves.
            get_tasks: dict[asyncio.Task[Message], asyncio.Queue[Message]] = {
                asyncio.create_task(q.get()): q for q in queues
            }
            try:
                while not stopped.is_set():
                    done, _ = await asyncio.wait(
                        [*get_tasks, stop_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if stop_task in done:
                        break
                    for task in list(done):
                        if task is stop_task:
                            continue
                        queue = get_tasks.pop(task)
                        message = task.result()
                        # Re-arm this queue before yielding so concurrent publishes
                        # keep flowing while the consumer processes the message.
                        get_tasks[asyncio.create_task(queue.get())] = queue
                        yield message
            finally:
                stop_task.cancel()
                for task in get_tasks:
                    task.cancel()

        return _iter()
