"""Unit tests for the copilot SSE proxy with a fake httpx transport (no network)."""

from __future__ import annotations

import httpx
import pytest
from edis_contracts.security import SecurityContext

from edis_gateway.config import GatewaySettings
from edis_gateway.proxy.copilot import CopilotProxy, CopilotUpstreamError


def _principal() -> SecurityContext:
    return SecurityContext(tenant_id="acme", user_id="u1", roles=["analyst"], scopes=[])


def _fake_client(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, base_url="http://copilot")


@pytest.mark.asyncio
async def test_proxy_streams_through_and_injects_tenant_header():
    seen_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        # The copilot must derive tenant from the forwarded header, not the body.
        assert request.headers["X-EDIS-Tenant"] == "acme"
        assert request.headers["X-EDIS-User"] == "u1"
        body = b"event: token\ndata: Revenue fell 8.3%\n\nevent: done\ndata: {}\n\n"
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    proxy = CopilotProxy(GatewaySettings())
    proxy._client = _fake_client(handler)

    chunks = [
        c async for c in proxy.stream_chat(_principal(), b'{"q":"why?","tenant_id":"globex"}')
    ]
    out = b"".join(chunks).decode()
    assert "Revenue fell 8.3%" in out
    assert "event: done" in out
    # Body claimed globex; the injected header is still the verified tenant.
    # (httpx lowercases header keys when copied into a plain dict.)
    assert seen_headers["x-edis-tenant"] == "acme"

    await proxy._client.aclose()


@pytest.mark.asyncio
async def test_proxy_maps_upstream_error_to_502():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"boom")

    proxy = CopilotProxy(GatewaySettings())
    proxy._client = _fake_client(handler)

    with pytest.raises(CopilotUpstreamError) as exc:
        async for _ in proxy.stream_chat(_principal(), b"{}"):
            pass
    assert exc.value.status == 502

    await proxy._client.aclose()


@pytest.mark.asyncio
async def test_proxy_maps_connect_failure_to_502():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    proxy = CopilotProxy(GatewaySettings())
    proxy._client = _fake_client(handler)

    with pytest.raises(CopilotUpstreamError):
        async for _ in proxy.stream_chat(_principal(), b"{}"):
            pass

    await proxy._client.aclose()
