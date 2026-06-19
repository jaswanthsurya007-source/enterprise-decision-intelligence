"""SSE stream routes: one ``StreamingResponse`` per concern (W1).

Three authenticated, tenant-scoped streams bridge the bus to the browser:

* ``GET /v1/stream/metrics``         <- ``edis.metrics.points.v1``
* ``GET /v1/stream/anomalies``       <- ``edis.findings.v1``
* ``GET /v1/stream/recommendations`` <- ``edis.decisions.recommendations.v1``

Each route resolves the verified :class:`SecurityContext` (tenant from the JWT,
never a query param), enforces a ``DATA_READ`` RBAC gate, builds a **fresh**
:class:`MessageSource` and a **unique** consumer group for the connection, and
returns a :class:`fastapi.responses.StreamingResponse` driven by
:func:`edis_gateway.sse.bridge.bridge_stream`. No extra SSE dependency is used.

The response carries the SSE headers the browser/proxies expect
(``Cache-Control: no-cache``, ``Connection: keep-alive``,
``X-Accel-Buffering: no`` to disable proxy buffering). On client disconnect the
bridge stops the source and releases the consumer subscription.
"""

from __future__ import annotations

import uuid

from edis_contracts import topics
from edis_contracts.security import SecurityContext
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from edis_gateway.deps import get_gateway_settings, get_source_factory, require_read
from edis_gateway.sse.bridge import (
    ANOMALY_EVENT,
    METRICS_EVENT,
    RECOMMENDATION_EVENT,
    Concern,
    bridge_stream,
)

router = APIRouter(tags=["sse"])

_SSE_MEDIA_TYPE = "text/event-stream"
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # disable nginx/proxy buffering of the stream
}

# The three bridged concerns. Tool/topic order is fixed and the id field matches
# each payload's primary id (best-effort Last-Event-ID hint).
_METRICS_CONCERN = Concern(topics.METRICS_POINTS, METRICS_EVENT, id_field=None)
_ANOMALIES_CONCERN = Concern(topics.FINDINGS, ANOMALY_EVENT, id_field="finding_id")
_RECOMMENDATIONS_CONCERN = Concern(
    topics.RECOMMENDATIONS, RECOMMENDATION_EVENT, id_field="recommendation_id"
)


def _streaming_response(request: Request, principal: SecurityContext, concern: Concern):
    """Build the tenant-scoped :class:`StreamingResponse` for ``concern``."""

    settings = get_gateway_settings(request)
    source_factory = get_source_factory(request)
    source = source_factory()
    group = f"{settings.sse_group_prefix}-{concern.event}-{uuid.uuid4().hex}"

    generator = bridge_stream(
        source=source,
        concern=concern,
        tenant_id=principal.tenant_id,
        group=group,
        heartbeat_seconds=settings.sse_heartbeat_seconds,
        is_disconnected=request.is_disconnected,
    )
    return StreamingResponse(
        generator,
        media_type=_SSE_MEDIA_TYPE,
        headers=_SSE_HEADERS,
    )


@router.get("/v1/stream/metrics", summary="Live KPI/metric tick stream (SSE)")
async def stream_metrics(
    request: Request,
    principal: SecurityContext = Depends(require_read("metric")),
) -> StreamingResponse:
    """Bridge ``edis.metrics.points.v1`` to the browser, scoped to the tenant."""

    return _streaming_response(request, principal, _METRICS_CONCERN)


@router.get("/v1/stream/anomalies", summary="Live anomaly (finding) stream (SSE)")
async def stream_anomalies(
    request: Request,
    principal: SecurityContext = Depends(require_read("finding")),
) -> StreamingResponse:
    """Bridge ``edis.findings.v1`` to the browser, scoped to the tenant."""

    return _streaming_response(request, principal, _ANOMALIES_CONCERN)


@router.get("/v1/stream/recommendations", summary="Live recommendation stream (SSE)")
async def stream_recommendations(
    request: Request,
    principal: SecurityContext = Depends(require_read("recommendation")),
) -> StreamingResponse:
    """Bridge ``edis.decisions.recommendations.v1`` to the browser, tenant-scoped."""

    return _streaming_response(request, principal, _RECOMMENDATIONS_CONCERN)
