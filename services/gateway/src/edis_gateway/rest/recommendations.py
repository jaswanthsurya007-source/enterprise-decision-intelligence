"""``/v1/recommendations`` — tenant-scoped recommendation snapshot from L4.

Returns the canonical :class:`~edis_contracts.decisions.Recommendation` payloads
(``edis.decisions.recommendations.v1`` shape) **sorted by priority** (best
``priority_rank`` first, then higher ``priority_score``, then newest), paginated,
optionally filtered by status. Tenant comes from the verified JWT.
"""

from __future__ import annotations

from edis_contracts.decisions import Recommendation
from edis_contracts.security import SecurityContext
from fastapi import APIRouter, Depends, Query

from edis_gateway.deps import get_repo, require_read

router = APIRouter(tags=["recommendations"])

_MAX_PAGE = 200


@router.get(
    "/v1/recommendations",
    response_model=list[Recommendation],
    summary="List recommendations (by priority)",
)
async def list_recommendations(
    principal: SecurityContext = Depends(require_read("recommendation")),
    repo=Depends(get_repo),
    limit: int = Query(50, ge=1, le=_MAX_PAGE),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, description="Filter by recommendation status."),
) -> list[Recommendation]:
    """Tenant-scoped, priority-sorted, paginated recommendation list."""

    return await repo.list_recommendations(
        principal.tenant_id,
        limit=limit,
        offset=offset,
        status=status,
    )
