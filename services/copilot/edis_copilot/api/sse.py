"""SSE framing + the streaming driver that bridges the agent loop to the response.

The copilot chat endpoint streams the answer as Server-Sent Events. Frame shapes (the
``type`` field discriminates), matching the gateway's transparent passthrough and the
dashboard's reader:

* ``route``    — ``{type, route}``                 the resolved routing decision
* ``token``    — ``{type, text}``                  an answer-text delta as it streams
* ``tool_call``— ``{type, tool, input}``           a tool the agent invoked this turn
* ``usage``    — ``{type, model, usage}``          per-call token usage (cache reads etc.)
* ``citation`` — ``{type, citation}``              one numbered citation (provenance)
* ``done``     — ``{type, answer, citations, facts_used, grounding_passed, confidence,…}``
                                                    the terminal frame with the full answer
* ``error``    — ``{type, detail}``                a terminal error frame (loop never raises,
                                                    but the driver guards the stream itself)

:func:`format_sse` mirrors the gateway encoder byte-for-byte (``event:``/``data:``/blank
line, JSON split across ``data:`` lines). :func:`run_chat_stream` runs the agent loop
with an ``emit`` callback that pushes frames onto a queue while the generator yields them
encoded — so tokens reach the browser live. Pure framing + a thin async bridge; no SDK
imported here.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from edis_platform.logging import get_logger

_log = get_logger(__name__)

SSE_MEDIA_TYPE = "text/event-stream"
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # disable proxy buffering so tokens arrive live
}

#: Sentinel pushed onto the frame queue to signal the producer is done.
_DONE = object()


def format_sse(payload: dict[str, Any]) -> bytes:
    """Frame one copilot SSE event. ``payload['type']`` becomes the SSE ``event:`` name.

    The JSON body is split on newlines into multiple ``data:`` lines so an embedded
    newline keeps the frame a single SSE event (WHATWG SSE). UTF-8 bytes, terminated by a
    blank line. Pure — mirrors the gateway's ``format_sse``.
    """

    event = str(payload.get("type", "message"))
    data = json.dumps(payload, default=str, separators=(",", ":"))
    lines = [f"event: {event}"]
    for line in data.split("\n"):
        lines.append(f"data: {line}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


async def run_chat_stream(
    run_answer: Callable[[Callable[[dict[str, Any]], Awaitable[None]]], Awaitable[Any]],
    *,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[bytes]:
    """Drive the agent loop, yielding each emitted frame as encoded SSE bytes.

    ``run_answer`` is a coroutine factory that takes the ``emit`` callback and runs the
    turn (the API wraps :func:`edis_copilot.agent.loop.answer` with the principal/registry/llm).
    Frames the loop emits are queued and yielded encoded as they arrive, so tokens stream
    live; a terminal ``error`` frame is emitted if the (otherwise non-raising) loop or the
    stream itself fails. Stops early if the client disconnects.
    """

    queue: asyncio.Queue[Any] = asyncio.Queue()

    async def emit(frame: dict[str, Any]) -> None:
        await queue.put(frame)

    async def producer() -> None:
        try:
            await run_answer(emit)
        except Exception as exc:  # noqa: BLE001 - loop is non-raising, but guard the stream
            _log.warning("copilot stream producer failed", extra={"error": str(exc)})
            await queue.put({"type": "error", "detail": "internal error"})
        finally:
            await queue.put(_DONE)

    task = asyncio.create_task(producer())
    try:
        while True:
            if is_disconnected is not None and await is_disconnected():
                break
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                # Heartbeat comment keeps proxies from closing an idle connection.
                yield b": keep-alive\n\n"
                continue
            if frame is _DONE:
                break
            yield format_sse(frame)
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
