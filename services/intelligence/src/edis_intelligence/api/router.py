"""L3 read API routes — tenant-scoped, paginated reads over persisted findings/forecasts.

Every data route is tenant-scoped via the verified :class:`SecurityContext` (the tenant
comes from the JWT, never a query param) and reads through the injected repo. Responses
are the canonical contract models (:class:`Finding` / :class:`Forecast`), so the gateway
gets exactly the ``edis.findings.v1`` / ``edis.forecasts.v1`` shapes. Errors surface as
RFC 9457 ``application/problem+json`` via the platform handlers.
"""

from __future__ import annotations

from uuid import UUID

from edis_contracts.findings import Finding, Forecast
from edis_contracts.security import SecurityContext
from edis_platform.errors import NotFoundError
from fastapi import APIRouter, Depends, Query

from edis_intelligence.api.deps import get_principal, get_repo

router = APIRouter(tags=["intelligence"])

_MAX_PAGE = 200


@router.get("/v1/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    """Always-ok liveness signal (unauthenticated; probes carry no JWT)."""

    return {"status": "ok", "service": "edis-intelligence"}


@router.get("/v1/findings", response_model=list[Finding], summary="List findings")
async def list_findings(
    principal: SecurityContext = Depends(get_principal),
    repo=Depends(get_repo),
    limit: int = Query(50, ge=1, le=_MAX_PAGE),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, description="Filter by finding status."),
    metric_key: str | None = Query(None, description="Filter by metric_key."),
) -> list[Finding]:
    """Tenant-scoped, paginated finding list (newest first)."""

    return await repo.list_findings(
        principal.tenant_id,
        limit=limit,
        offset=offset,
        status=status,
        metric_key=metric_key,
    )


@router.get("/v1/findings/{finding_id}", response_model=Finding, summary="Get one finding")
async def get_finding(
    finding_id: UUID,
    principal: SecurityContext = Depends(get_principal),
    repo=Depends(get_repo),
) -> Finding:
    """Fetch a single finding by id within the caller's tenant (404 if not found)."""

    finding = await repo.get_finding(principal.tenant_id, finding_id)
    if finding is None:
        raise NotFoundError(f"No finding {finding_id} for this tenant.")
    return finding


@router.get("/v1/forecasts", response_model=list[Forecast], summary="List forecasts")
async def list_forecasts(
    principal: SecurityContext = Depends(get_principal),
    repo=Depends(get_repo),
    limit: int = Query(50, ge=1, le=_MAX_PAGE),
    offset: int = Query(0, ge=0),
    metric_key: str | None = Query(None, description="Filter by metric_key."),
) -> list[Forecast]:
    """Tenant-scoped, paginated forecast list (newest first)."""

    return await repo.list_forecasts(
        principal.tenant_id,
        limit=limit,
        offset=offset,
        metric_key=metric_key,
    )
