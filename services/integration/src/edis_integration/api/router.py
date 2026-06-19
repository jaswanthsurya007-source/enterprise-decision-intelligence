"""Ops/admin API for the integration (L2) service.

Operational surface only -- L2 is a stream processor, not a request/response data
API (that is the gateway's job). These routes give operators visibility and a
recovery lever:

* ``GET  /v1/health``               -- liveness (the process is up).
* ``GET  /v1/integration/lag``      -- pending (staged-but-unpublished) outbox
  depth + the consumer's processed/persisted/quarantined counters: the outbox
  relay backlog, i.e. how far the published view trails the persisted view.
* ``GET  /v1/integration/quarantine`` -- recent :class:`QuarantinedRecord`s (the
  records that terminated in the DLQ rather than the canonical store).
* ``POST /v1/integration/reprocess`` -- re-run a set of quarantined records (or
  arbitrary raw envelopes) through the batch loader -- the replay lever.

Every handler reads collaborators off ``app.state`` (set by the app factory /
lifespan); when a collaborator is absent (e.g. the bare app with no DB wired) the
route degrades to a safe, explicit response rather than raising. Errors surface as
RFC 9457 ``application/problem+json`` via the platform handlers.
"""

from __future__ import annotations

from typing import Any

from edis_platform.errors import NotFoundError
from fastapi import APIRouter, Body, status
from starlette.requests import Request

router = APIRouter(tags=["integration"])


@router.get("/v1/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    """Always-ok liveness signal (unauthenticated; probes carry no JWT)."""

    return {"status": "ok", "service": "edis-integration"}


@router.get("/v1/integration/lag", summary="Outbox relay backlog + consumer counters")
async def lag(request: Request) -> dict[str, Any]:
    """Report the staged-but-unpublished outbox depth and processing counters.

    ``pending`` is the number of outbox rows the relay has not yet published --
    the gap between the persisted and published views. ``consumer`` echoes the
    stream consumer's counters when one is running in-process.
    """

    state = request.app.state
    pending: int | None = None
    reader = getattr(state, "outbox_reader", None)
    if reader is not None and hasattr(reader, "pending_count"):
        pending = await reader.pending_count()

    consumer = getattr(state, "consumer", None)
    counters = (
        {
            "processed": consumer.processed,
            "persisted": consumer.persisted,
            "quarantined": consumer.quarantined,
            "duplicates": consumer.duplicates,
        }
        if consumer is not None
        else None
    )

    return {
        "outbox_pending": pending,
        "consumer": counters,
        "backend": getattr(state, "platform_settings", None)
        and state.platform_settings.sink_backend,
    }


@router.get("/v1/integration/quarantine", summary="Recent quarantined records")
async def quarantine(request: Request, limit: int = 100) -> dict[str, Any]:
    """List recent quarantined records (DQ failures / un-mappable records).

    Reads through ``app.state.quarantine_repo`` when wired; otherwise returns an
    empty list (the bare app has no persistence) so the route is always callable.
    """

    repo = getattr(request.app.state, "quarantine_repo", None)
    if repo is None:
        return {"items": [], "count": 0, "persisted": False}
    items = await repo.list_quarantined(limit=min(max(limit, 1), 1000))
    return {"items": items, "count": len(items), "persisted": True}


@router.post(
    "/v1/integration/reprocess",
    summary="Replay quarantined records / raw envelopes through the batch loader",
    status_code=status.HTTP_202_ACCEPTED,
)
async def reprocess(
    request: Request,
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Re-run records through the batch loader (the replay lever).

    Accepts ``{"quarantine_ids": [...]}`` to replay persisted quarantines, or
    ``{"envelopes": [...]}`` to replay arbitrary raw envelopes. Requires a
    :class:`~edis_integration.consumers.batch_loader.BatchLoader` on
    ``app.state.batch_loader``; absent one (bare app) it is a no-op 404 so the
    contract is explicit rather than silently dropping the request.
    """

    from edis_contracts.ingest import IngestEnvelope

    state = request.app.state
    loader = getattr(state, "batch_loader", None)
    if loader is None:
        raise NotFoundError("no batch loader configured; persistence is not wired")

    envelopes: list[IngestEnvelope] = []

    raw_envelopes = body.get("envelopes") or []
    for raw in raw_envelopes:
        envelopes.append(IngestEnvelope.model_validate(raw))

    quarantine_ids = body.get("quarantine_ids") or []
    repo = getattr(state, "quarantine_repo", None)
    if quarantine_ids and repo is not None:
        for env in await repo.envelopes_for(quarantine_ids):
            envelopes.append(env)

    result = await loader.load(envelopes)
    return {
        "accepted": True,
        "processed": result.processed,
        "persisted": result.persisted,
        "quarantined": result.quarantined,
        "duplicates": result.duplicates,
        "published": result.published,
    }


__all__ = ["router"]
