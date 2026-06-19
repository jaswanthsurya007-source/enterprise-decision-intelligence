"""``/v1/kpis`` — tenant-scoped KPI snapshot from the L2 daily metric rollup.

Returns one :class:`~edis_gateway.models.KpiSnapshot` per metric (optionally
filtered by ``metric_key``): the latest daily-rollup value plus its
week-over-week delta. Every number originates in the L2 continuous aggregate; the
gateway only reshapes it. Tenant comes from the verified JWT (``require_read``).
"""

from __future__ import annotations

from edis_contracts.security import SecurityContext
from fastapi import APIRouter, Depends, Query

from edis_gateway.deps import get_repo, require_read
from edis_gateway.models import KpiSnapshot

router = APIRouter(tags=["kpis"])

_MAX_PAGE = 200


@router.get("/v1/kpis", response_model=list[KpiSnapshot], summary="List KPI snapshots")
async def list_kpis(
    principal: SecurityContext = Depends(require_read("kpi")),
    repo=Depends(get_repo),
    metric_key: str | None = Query(None, description="Filter to a single metric_key."),
    limit: int = Query(50, ge=1, le=_MAX_PAGE),
) -> list[KpiSnapshot]:
    """Tenant-scoped KPI tiles from the L2 daily rollup (one per metric)."""

    return await repo.list_kpis(principal.tenant_id, metric_key=metric_key, limit=limit)
