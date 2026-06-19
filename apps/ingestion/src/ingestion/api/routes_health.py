"""Health routes — liveness and readiness (unauthenticated).

* ``GET /v1/health``       — liveness: the process is up and serving.
* ``GET /v1/health/ready`` — readiness: the shared collaborators are *started*
  (sink + idempotency guard). Returns ``200`` when ready, ``503`` otherwise, so an
  orchestrator only routes traffic once the bus/dedupe backends are live.

These probes never touch the network themselves (they read the started-flags the
lifespan handler sets), so they are cheap and cannot hang. They are deliberately
unauthenticated — probes carry no JWT.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status
from starlette.requests import Request

router = APIRouter(prefix="/v1/health", tags=["health"])


def _started(obj: Any) -> bool:
    """Best-effort 'has this collaborator been started?' check.

    The in-proc sink / in-memory guard expose a private ``_started`` flag; absent
    one we optimistically assume readiness (a backend with no explicit lifecycle).
    """

    return bool(getattr(obj, "_started", True))


@router.get("", summary="Liveness probe")
async def health() -> dict[str, str]:
    """Always-ok liveness signal."""

    return {"status": "ok", "service": "ingestion"}


@router.get("/ready", summary="Readiness probe")
async def ready(request: Request, response: Response) -> dict[str, Any]:
    """Readiness: the sink and idempotency guard have been started."""

    state = request.app.state
    sink_ready = _started(getattr(state, "sink", None))
    idem_ready = _started(getattr(state, "idempotency", None))
    writer_present = getattr(state, "writer", None) is not None
    is_ready = sink_ready and idem_ready

    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if is_ready else "not_ready",
        "checks": {
            "sink": sink_ready,
            "idempotency": idem_ready,
            "writer_configured": writer_present,
        },
    }
