"""REST routes for recommendations + their lifecycle (``/v1/recommendations``).

Endpoints (all JWT + tenant scoped, RBAC-gated; ``tenant_id`` comes ONLY from the verified
:class:`~edis_contracts.security.SecurityContext`):

* ``GET  /v1/recommendations``            -- list the caller's tenant's recommendations
                                             (rank 1 first), optional ``status`` filter +
                                             ``limit``/``offset`` pagination. Needs
                                             ``DATA_READ:recommendation``.
* ``GET  /v1/recommendations/{id}``        -- one recommendation (404 if absent / other
                                             tenant). Needs ``DATA_READ:recommendation``.
* ``POST /v1/recommendations/{id}/accept`` -- ``proposed -> accepted``; 200 + the updated
                                             recommendation; 409 if illegal (e.g. already
                                             accepted); 404 if absent. Needs
                                             ``accept:recommendation`` (operator/admin).
* ``POST /v1/recommendations/{id}/reject`` -- ``proposed -> rejected``; same semantics with
                                             ``reject:recommendation``.

Every transition runs through the :class:`~decision_engine.lifecycle.manager.LifecycleManager`
so the FSM gate, lifecycle event, and audit emission all happen exactly once. Illegal moves
raise :class:`~edis_platform.errors.ConflictError` -> HTTP 409 ``problem+json``; unknown ids
raise :class:`~edis_platform.errors.NotFoundError` -> 404; auth failures map to 401/403.
"""

from __future__ import annotations

from uuid import UUID

from edis_contracts.decisions import Recommendation
from edis_contracts.security import SecurityContext
from fastapi import APIRouter, Depends, Query
from starlette.requests import Request

from decision_engine.api.deps import (
    get_lifecycle_manager,
    get_repository,
    require_recommendation,
)

router = APIRouter(prefix="/v1/recommendations", tags=["recommendations"])


@router.get("", response_model=list[Recommendation])
async def list_recommendations(
    request: Request,
    status: str | None = Query(default=None, description="Optional status filter."),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: SecurityContext = Depends(require_recommendation("DATA_READ")),
    repo=Depends(get_repository),
) -> list[Recommendation]:
    """List the caller's tenant's recommendations (priority rank 1 first)."""

    return await repo.list_for_tenant(ctx.tenant_id, status=status, limit=limit, offset=offset)


@router.get("/{recommendation_id}", response_model=Recommendation)
async def get_recommendation(
    recommendation_id: UUID,
    ctx: SecurityContext = Depends(require_recommendation("DATA_READ")),
    repo=Depends(get_repository),
) -> Recommendation:
    """Fetch one tenant-scoped recommendation (404 if absent or another tenant's)."""

    from edis_platform.errors import NotFoundError

    rec = await repo.get(ctx.tenant_id, recommendation_id)
    if rec is None:
        raise NotFoundError(f"Recommendation '{recommendation_id}' not found.")
    return rec


@router.post("/{recommendation_id}/accept", response_model=Recommendation)
async def accept_recommendation(
    recommendation_id: UUID,
    ctx: SecurityContext = Depends(require_recommendation("accept")),
    manager=Depends(get_lifecycle_manager),
) -> Recommendation:
    """Accept a recommendation (``proposed -> accepted``). 409 if already terminal."""

    return await manager.transition(ctx.tenant_id, recommendation_id, "accepted", ctx=ctx)


@router.post("/{recommendation_id}/reject", response_model=Recommendation)
async def reject_recommendation(
    recommendation_id: UUID,
    ctx: SecurityContext = Depends(require_recommendation("reject")),
    manager=Depends(get_lifecycle_manager),
) -> Recommendation:
    """Reject a recommendation (``proposed -> rejected``). 409 if already terminal."""

    return await manager.transition(ctx.tenant_id, recommendation_id, "rejected", ctx=ctx)
