"""``/v1/forecasts`` — tenant-scoped forecast snapshot from L3.

Returns the canonical :class:`~edis_contracts.findings.Forecast` payloads
(``edis.forecasts.v1`` shape) newest-first, paginated, optionally filtered by
metric_key. The dashboard draws the actual-vs-forecast band from these. Tenant
comes from the verified JWT (``require_read``).
"""

from __future__ import annotations

from edis_contracts.findings import Forecast
from edis_contracts.security import SecurityContext
from fastapi import APIRouter, Depends, Query

from edis_gateway.deps import get_repo, require_read

router = APIRouter(tags=["forecasts"])

_MAX_PAGE = 200


@router.get("/v1/forecasts", response_model=list[Forecast], summary="List forecasts")
async def list_forecasts(
    principal: SecurityContext = Depends(require_read("forecast")),
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
