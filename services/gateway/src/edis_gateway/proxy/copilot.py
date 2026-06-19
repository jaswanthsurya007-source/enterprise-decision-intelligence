"""Copilot SSE passthrough proxy (W1).

``POST /v1/copilot/chat`` is the single browser entry point for the copilot. The
gateway:

1. **Authenticates + scopes at the edge.** The bearer JWT is validated into a
   :class:`SecurityContext` and an ``AI_QUERY`` RBAC gate is enforced *here* — the
   gateway is the authoritative authorization boundary. The browser cannot reach
   the copilot directly.
2. **Injects the verified identity server-side.** The tenant/user/roles are taken
   ONLY from the verified token and forwarded to the copilot as trusted headers
   (``X-EDIS-Tenant`` / ``X-EDIS-User`` / ``X-EDIS-Roles``). The client-supplied
   request body is forwarded for the *question*, but the copilot derives tenant
   from the forwarded header, never the body — so a body cannot spoof a tenant.
3. **Streams through, byte-for-byte.** The upstream copilot SSE response is
   relayed to the browser unbuffered via ``httpx`` streaming, so tokens, tool
   traces, citations, and usage frames arrive live. On client disconnect the
   upstream request is cancelled.

The :class:`CopilotProxy` holds a shared ``httpx.AsyncClient`` (a long-lived
connection pool, started/stopped by the app lifespan). The proxy never parses or
rewrites the SSE body — it is a transparent relay — so the copilot's exact frame
shape reaches the dashboard.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from edis_contracts.security import ResourceRef, SecurityContext
from edis_platform.authz.rbac import evaluate
from edis_platform.errors import EdisError, ForbiddenError
from edis_platform.logging import get_logger
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from edis_gateway.deps import get_copilot_proxy, get_principal

if TYPE_CHECKING:
    from edis_gateway.config import GatewaySettings

_log = get_logger(__name__)

router = APIRouter(tags=["copilot"])

_SSE_MEDIA_TYPE = "text/event-stream"
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class CopilotUpstreamError(EdisError):
    """The copilot service is unreachable or returned a non-OK status."""

    status = 502
    title = "Bad Gateway"
    problem_type = "urn:edis:problem:copilot-upstream"


class CopilotProxy:
    """Holds the shared ``httpx`` client and relays the copilot SSE stream.

    Built by the app factory; the client is created in :meth:`start` and closed in
    :meth:`stop` (lifespan-managed). Importing this module opens no connection.
    """

    def __init__(self, settings: "GatewaySettings") -> None:
        self._settings = settings
        self._client = None  # type: ignore[var-annotated]

    async def start(self) -> None:
        import httpx

        # No total read timeout: the SSE stream is long-lived. Bound only the
        # connect/initial-response latency so a dead copilot fails fast.
        timeout = httpx.Timeout(
            self._settings.copilot_connect_timeout_seconds,
            read=None,
            write=self._settings.copilot_connect_timeout_seconds,
            pool=self._settings.copilot_connect_timeout_seconds,
        )
        self._client = httpx.AsyncClient(
            base_url=self._settings.copilot_base_url,
            timeout=timeout,
        )

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def stream_chat(self, principal: SecurityContext, body: bytes) -> AsyncIterator[bytes]:
        """Open the upstream copilot chat and yield its SSE bytes unbuffered.

        Forwards the request ``body`` (the question) and the **server-side**
        identity headers derived from ``principal`` (tenant never from the body).
        Yields each upstream chunk as it arrives; closes the upstream on client
        disconnect / cancellation. Raises :class:`CopilotUpstreamError` if the
        copilot is unreachable or replies non-2xx before streaming begins.
        """

        import httpx

        if self._client is None:
            raise CopilotUpstreamError("Copilot proxy client is not started.")

        headers = {
            "Content-Type": "application/json",
            "Accept": _SSE_MEDIA_TYPE,
            # Server-side identity injection: the copilot trusts these because the
            # gateway is the authoritative boundary. Tenant is NEVER taken from the
            # forwarded body.
            "X-EDIS-Tenant": principal.tenant_id,
            "X-EDIS-User": principal.user_id,
            "X-EDIS-Roles": ",".join(principal.roles),
        }
        try:
            async with self._client.stream(
                "POST",
                self._settings.copilot_chat_path,
                content=body,
                headers=headers,
            ) as upstream:
                if upstream.status_code >= 400:
                    # Drain so the connection can be reused, then surface as 502.
                    await upstream.aread()
                    _log.warning(
                        "copilot upstream returned error status",
                        extra={
                            "status_code": upstream.status_code,
                            "tenant_id": principal.tenant_id,
                        },
                    )
                    raise CopilotUpstreamError(f"Copilot service returned {upstream.status_code}.")
                # aiter_bytes yields the decoded body as it arrives (SSE is
                # uncompressed text, so this is a transparent relay) and works
                # uniformly across real streaming and buffered transports.
                async for chunk in upstream.aiter_bytes():
                    if chunk:
                        yield chunk
        except httpx.HTTPError as exc:
            _log.warning(
                "copilot upstream connection failed",
                extra={"tenant_id": principal.tenant_id, "error": str(exc)},
            )
            raise CopilotUpstreamError("Copilot service is unreachable.") from exc


@router.post("/v1/copilot/chat", summary="Proxy the copilot chat SSE stream")
async def copilot_chat(
    request: Request,
    principal: SecurityContext = Depends(get_principal),
    proxy: CopilotProxy = Depends(get_copilot_proxy),
) -> StreamingResponse:
    """Authenticate + scope at the edge, then stream the copilot answer over SSE.

    Enforces an ``AI_QUERY`` RBAC gate (analyst/operator/admin). The request body
    is forwarded for the question; tenant is injected from the verified token, so
    a body cannot cross tenants.
    """

    if not evaluate(principal, "AI_QUERY", ResourceRef(type="copilot")):
        raise ForbiddenError("Requires AI_QUERY on copilot.")

    body = await request.body()
    return StreamingResponse(
        proxy.stream_chat(principal, body),
        media_type=_SSE_MEDIA_TYPE,
        headers=_SSE_HEADERS,
    )
