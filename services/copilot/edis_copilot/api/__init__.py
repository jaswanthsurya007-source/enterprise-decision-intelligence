"""Copilot HTTP API (P2): chat SSE, conversations, and the SSE framing.

* :mod:`edis_copilot.api.chat` — ``POST /v1/copilot/chat``, the streaming grounded turn (SSE).
* :mod:`edis_copilot.api.conversations` — ``GET /v1/copilot/conversations``, tenant-scoped list.
* :mod:`edis_copilot.api.sse` — :func:`format_sse` (the frame encoder, mirroring the gateway) and
  :func:`run_chat_stream` (the agent-loop -> SSE bridge).

The routers mount onto the app in :func:`edis_copilot.main.create_app`. ``GET /v1/health`` lives
on the app factory. Tenant is always taken from the verified principal, never the body.
"""

from __future__ import annotations

from edis_copilot.api.chat import router as chat_router
from edis_copilot.api.conversations import router as conversations_router
from edis_copilot.api.sse import format_sse, run_chat_stream

__all__ = ["chat_router", "conversations_router", "format_sse", "run_chat_stream"]
