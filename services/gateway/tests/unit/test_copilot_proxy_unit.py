"""P3 — the copilot SSE proxy: forwards a stub copilot's stream; JWT + RBAC enforced.

Two layers, both offline:

* the :class:`CopilotProxy` relay with a fake ``httpx`` transport (no network) — it streams
  the upstream SSE bytes through unchanged and injects the VERIFIED tenant as a server-side
  header (a body claiming another tenant cannot spoof it); upstream errors map to 502;
* the route through the real gateway app (``tests/conftest.py``) — the ``AI_QUERY`` RBAC
  gate is enforced at the edge: no JWT -> 401, a viewer -> 403, both RFC 9457 problems.
"""

from __future__ import annotations

import httpx
import pytest
from edis_contracts.security import SecurityContext

from edis_gateway.config import GatewaySettings
from edis_gateway.proxy.copilot import CopilotProxy, CopilotUpstreamError


def _principal() -> SecurityContext:
    return SecurityContext(tenant_id="acme", user_id="u1", roles=["analyst"], scopes=[])


def _fake_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://copilot")


@pytest.mark.asyncio
async def test_proxy_forwards_stream_and_injects_verified_tenant():
    """The proxy relays the stub copilot's SSE bytes and injects the verified tenant header."""

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        # The copilot trusts the forwarded header — body must NOT be able to spoof tenant.
        assert request.headers["x-edis-tenant"] == "acme"
        assert request.headers["x-edis-user"] == "u1"
        body = (
            b'event: route\ndata: {"intent":"rca"}\n\n'
            b"event: token\ndata: Revenue fell 8.3%\n\n"
            b'event: done\ndata: {"grounding_passed":true}\n\n'
        )
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    proxy = CopilotProxy(GatewaySettings())
    proxy._client = _fake_client(handler)
    try:
        chunks = [
            c async for c in proxy.stream_chat(_principal(), b'{"q":"why?","tenant_id":"globex"}')
        ]
        out = b"".join(chunks).decode()
        # The stream is relayed through verbatim — all three frames arrive intact.
        assert "event: route" in out
        assert "Revenue fell 8.3%" in out
        assert "event: done" in out
        # Body claimed globex; the injected header is still the verified acme.
        assert seen["x-edis-tenant"] == "acme"
    finally:
        await proxy._client.aclose()


@pytest.mark.asyncio
async def test_proxy_maps_upstream_5xx_to_502():
    """A non-2xx upstream status before streaming surfaces as a 502 CopilotUpstreamError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"boom")

    proxy = CopilotProxy(GatewaySettings())
    proxy._client = _fake_client(handler)
    try:
        with pytest.raises(CopilotUpstreamError) as exc:
            async for _ in proxy.stream_chat(_principal(), b"{}"):
                pass
        assert exc.value.status == 502
    finally:
        await proxy._client.aclose()


@pytest.mark.asyncio
async def test_proxy_maps_connect_failure_to_502():
    """An unreachable copilot (connect error) also maps to a 502 CopilotUpstreamError."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    proxy = CopilotProxy(GatewaySettings())
    proxy._client = _fake_client(handler)
    try:
        with pytest.raises(CopilotUpstreamError):
            async for _ in proxy.stream_chat(_principal(), b"{}"):
                pass
    finally:
        await proxy._client.aclose()


def test_route_requires_jwt(client):
    """The proxy route rejects an unauthenticated request with a 401 problem response."""

    res = client.post("/v1/copilot/chat", content=b"{}")
    assert res.status_code == 401
    assert res.headers["content-type"].startswith("application/problem+json")


def test_route_enforces_ai_query_rbac(client, viewer_token):
    """A viewer (no AI_QUERY) is forbidden at the edge before any upstream call."""

    headers = {"Authorization": f"Bearer {viewer_token}"}
    res = client.post("/v1/copilot/chat", headers=headers, content=b"{}")
    assert res.status_code == 403
    assert res.headers["content-type"].startswith("application/problem+json")
