"""SSE passthrough proxy to the L5 copilot service (W1).

:mod:`edis_gateway.proxy.copilot` streams ``POST /v1/copilot/chat`` from the
copilot service straight through to the browser over SSE via ``httpx`` streaming,
with the JWT + tenant enforced **here** at the edge (the gateway is the
authoritative authorization boundary; the copilot trusts the forwarded,
gateway-verified identity headers).
"""

from __future__ import annotations

__all__ = ["copilot"]
