"""The Kafka->browser SSE bridge core (transport-agnostic, unit-testable).

A bridge subscribes a single bus concern via a :class:`MessageSource` and yields
**SSE-framed** byte chunks, one per matching event, interleaved with periodic
heartbeats. It is deliberately decoupled from FastAPI so it can be unit-tested
with the in-process bus and a fake source — no broker, no web server.

Three guarantees the bridge enforces, all load-bearing for the edge:

1. **Tenant scoping.** Every event carries ``tenant_id``; the bridge drops any
   event whose tenant does not equal the connection's verified tenant. The bus is
   shared across tenants, so this filter — not the topic — is the isolation
   boundary. The tenant comes from the route's JWT, never the event/body.
2. **Heartbeat.** When no event arrives within ``heartbeat_seconds`` the bridge
   emits an SSE comment (``: hb\\n\\n``) so the browser (and intervening proxies)
   keep the connection alive and can detect a dead stream for backoff/reconnect.
3. **Clean teardown.** On client disconnect / cancellation the underlying source
   is stopped and the consumer-group subscription is closed, so a dropped browser
   never leaks a consumer.

SSE wire format (per the WHATWG spec): each frame is ``event: <name>\\n`` then one
or more ``data: <line>\\n`` lines then a blank line. ``id:`` carries a best-effort
event id for ``Last-Event-ID`` resumption hints. Multi-line JSON is split across
``data:`` lines so a payload containing newlines stays a single SSE event.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from edis_platform.bus.base import Message, MessageSource
from edis_platform.logging import get_logger

_log = get_logger(__name__)

#: Default SSE event name per concern (what the browser listens for).
METRICS_EVENT = "metric"
ANOMALY_EVENT = "anomaly"
RECOMMENDATION_EVENT = "recommendation"

_HEARTBEAT_FRAME = b": hb\n\n"


def format_sse(data: str, *, event: str | None = None, event_id: str | None = None) -> bytes:
    """Frame ``data`` as one SSE event, UTF-8 encoded.

    ``data`` is split on newlines into multiple ``data:`` lines so a JSON payload
    with embedded newlines remains a single SSE event. A trailing blank line
    terminates the event. Pure; no I/O.
    """

    lines: list[str] = []
    if event:
        lines.append(f"event: {event}")
    if event_id:
        lines.append(f"id: {event_id}")
    for line in data.split("\n"):
        lines.append(f"data: {line}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


@dataclass(frozen=True)
class Concern:
    """A single SSE concern: which topic to bridge and how to frame it.

    ``event`` is the SSE event name the browser subscribes to; ``id_field`` is the
    payload key used as the SSE ``id:`` (best-effort resumption hint).
    """

    topic: str
    event: str
    id_field: str | None = None


def event_id_for(concern: Concern, payload: dict) -> str | None:
    """Best-effort SSE ``id:`` for ``payload`` (the concern's id field, stringified)."""

    if concern.id_field is None:
        return None
    value = payload.get(concern.id_field)
    return str(value) if value is not None else None


async def bridge_stream(
    *,
    source: MessageSource,
    concern: Concern,
    tenant_id: str,
    group: str,
    heartbeat_seconds: float = 15.0,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[bytes]:
    """Yield SSE-framed bytes for ``concern``, scoped to ``tenant_id``.

    Starts ``source``, subscribes ``[concern.topic]`` under ``group`` (a unique
    per-connection group so no two connections share partitions), and yields:

    * one SSE event per **tenant-matching** message (other tenants' events are
      dropped — the isolation boundary), framed with the concern's event name and
      a best-effort ``id:``;
    * an SSE heartbeat comment whenever ``heartbeat_seconds`` elapse with no event.

    Exits cleanly when ``is_disconnected`` reports the client is gone, when the
    iterator is cancelled (browser closed the stream), and always stops the source
    in ``finally`` so the consumer subscription is released. Never raises into the
    response: an unexpected error is logged and ends the stream.
    """

    await source.start()
    stream = source.subscribe([concern.topic], group=group)
    next_task: asyncio.Task[Message] | None = None
    try:
        # Open frame: a comment so the browser sees bytes immediately (some
        # proxies buffer until first byte) and EventSource fires `open`.
        yield b": connected\n\n"
        while True:
            if is_disconnected is not None and await is_disconnected():
                break
            if next_task is None:
                next_task = asyncio.ensure_future(_anext(stream))
            done, _ = await asyncio.wait({next_task}, timeout=heartbeat_seconds)
            if next_task not in done:
                # Timed out waiting -> heartbeat, keep the same in-flight get.
                yield _HEARTBEAT_FRAME
                continue
            msg = next_task.result()
            next_task = None
            if msg is None:  # source exhausted / stopped
                break
            frame = _frame_message(concern, tenant_id, msg)
            if frame is not None:
                yield frame
    except asyncio.CancelledError:  # client disconnected mid-await
        raise
    except Exception as exc:  # noqa: BLE001 - never propagate into the HTTP response
        _log.warning(
            "sse bridge error; closing stream",
            extra={"topic": concern.topic, "tenant_id": tenant_id, "error": str(exc)},
        )
    finally:
        if next_task is not None and not next_task.done():
            next_task.cancel()
        try:
            await source.stop()
        except Exception:  # noqa: BLE001
            pass


def _frame_message(concern: Concern, tenant_id: str, msg: Message) -> bytes | None:
    """Frame ``msg`` as an SSE event iff it belongs to ``tenant_id``; else ``None``.

    ``msg.value`` is the JSON-decoded payload dict (as every bus backend delivers
    it). The tenant filter is the isolation boundary: a payload for another tenant
    is silently dropped. The event id key is also matched defensively.
    """

    payload = msg.value
    if not isinstance(payload, dict):
        return None
    if payload.get("tenant_id") != tenant_id:
        return None  # cross-tenant event on a shared topic -> drop
    data = json.dumps(payload, default=str, separators=(",", ":"))
    return format_sse(data, event=concern.event, event_id=event_id_for(concern, payload))


async def _anext(stream: AsyncIterator[Message]) -> Message | None:
    """``anext(stream)`` returning ``None`` at exhaustion (so the loop can break)."""

    try:
        return await stream.__anext__()
    except StopAsyncIteration:
        return None
