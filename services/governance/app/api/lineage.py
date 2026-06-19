"""``GET /v1/lineage/{entity_type}/{entity_id}`` -- trace an entity's lineage.

Returns every materialized edge where the entity is the source OR the destination
(raw -> canonical -> metric -> finding -> decision), tenant-scoped. Requires the
``auditor`` role (lineage reads are governance reads). The edges are folded from
``edis.governance.lineage.v1`` events by the lineage consumer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from edis_contracts.security import ResourceRef, SecurityContext
from edis_platform.authz.rbac import evaluate
from edis_platform.errors import ForbiddenError
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.deps import get_lineage_repo, require_role
from app.repo import LineageRepository

router = APIRouter(prefix="/v1/lineage", tags=["lineage"])

_require_auditor = require_role("auditor")


class LineageEdgeOut(BaseModel):
    """One lineage edge in the entity's graph."""

    lineage_edge_id: UUID
    lineage_id: UUID
    run_id: UUID
    tenant_id: str
    src_type: str
    src_id: str
    dst_type: str
    dst_id: str
    stage: str
    occurred_at: datetime


class LineageGraph(BaseModel):
    """The queried entity plus its incident edges."""

    entity_type: str
    entity_id: str
    edges: list[LineageEdgeOut]
    count: int


@router.get("/{entity_type}/{entity_id}", response_model=LineageGraph)
async def get_lineage(
    entity_type: str,
    entity_id: str,
    ctx: Annotated[SecurityContext, Depends(_require_auditor)],
    repo: Annotated[LineageRepository, Depends(get_lineage_repo)],
    limit: int = Query(default=200, ge=1, le=1000),
) -> LineageGraph:
    """Return all lineage edges incident to ``(entity_type, entity_id)``."""

    if not evaluate(ctx, "DATA_READ", ResourceRef(type="lineage")):
        raise ForbiddenError("Not permitted to read lineage.")

    rows = await repo.edges_for_entity(ctx.tenant_id, entity_type, entity_id, limit=limit)
    edges = [
        LineageEdgeOut(
            lineage_edge_id=r.lineage_edge_id,
            lineage_id=r.lineage_id,
            run_id=r.run_id,
            tenant_id=r.tenant_id,
            src_type=r.src_type,
            src_id=r.src_id,
            dst_type=r.dst_type,
            dst_id=r.dst_id,
            stage=r.stage,
            occurred_at=r.occurred_at,
        )
        for r in rows
    ]
    return LineageGraph(
        entity_type=entity_type,
        entity_id=entity_id,
        edges=edges,
        count=len(edges),
    )
