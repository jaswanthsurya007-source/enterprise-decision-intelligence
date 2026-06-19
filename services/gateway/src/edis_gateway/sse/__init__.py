"""Server-Sent Events: the gateway's Kafka->browser realtime bridge (W1).

One ``text/event-stream`` per concern at ``/v1/stream/{metrics,anomalies,
recommendations}``. :mod:`edis_gateway.sse.bridge` holds the transport-agnostic
core (SSE wire framing, per-tenant filtering, heartbeat, clean teardown);
:mod:`edis_gateway.sse.stream` wires those into authenticated FastAPI routes that
return a :class:`fastapi.responses.StreamingResponse` (no extra dependency).
"""

from __future__ import annotations

__all__ = ["bridge", "stream"]
