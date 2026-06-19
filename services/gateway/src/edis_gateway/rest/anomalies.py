"""``/v1/anomalies`` — tenant-scoped anomaly snapshot from L3 findings.

Returns the canonical :class:`~edis_contracts.findings.Finding` payloads
(``edis.findings.v1`` shape) newest-first, paginated, optionally filtered by
status / metric_key. Tenant comes from the verified JWT (``require_read``).
"""

from __future__ import annotations

from edis_contracts.findings import Finding
from edis_contracts.security import SecurityContext
from fastapi import APIRouter, Depends, Query

from edis_gateway.deps import get_repo, require_read

router = APIRouter(tags=["anomalies"])

_MAX_PAGE = 200


@router.get("/v1/anomalies", response_model=list[Finding], summary="List anomalies (findings)")
async def list_anomalies(
    principal: SecurityContext = Depends(require_read("finding")),
    repo=Depends(get_repo),
    limit: int = Query(50, ge=1, le=_MAX_PAGE),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, description="Filter by finding status."),
    metric_key: str | None = Query(None, description="Filter by metric_key."),
) -> list[Finding]:
    """Tenant-scoped, paginated anomaly list (newest first)."""

    return await repo.list_anomalies(
        principal.tenant_id,
        limit=limit,
        offset=offset,
        status=status,
        metric_key=metric_key,
    )
