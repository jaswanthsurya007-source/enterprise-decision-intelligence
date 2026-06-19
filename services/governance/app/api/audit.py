"""``GET /v1/audit`` -- read the append-only audit log (auditor/admin only).

Tenant-scoped (rows come only from the caller's verified ``tenant_id`` -- never a
query param) and paginated. The ``auditor`` role (and ``admin``) is required;
``require_role`` raises a 403 ``problem+json`` otherwise. RBAC for the audit
*resource* is also asserted via the pure ``evaluate()`` so the role->permission
matrix stays the single source of truth.
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

from app.deps import get_audit_repo, require_role
from app.repo import AuditRepository

router = APIRouter(prefix="/v1/audit", tags=["audit"])

# auditor or admin may read the audit log.
_require_auditor = require_role("auditor")


class AuditRecord(BaseModel):
    """One audit row as returned to clients (the AuditEvent projection + raw)."""

    audit_id: UUID
    occurred_at: datetime
    tenant_id: str
    actor: dict
    action: str
    resource: dict
    outcome: str
    reason: str | None = None
    decision_id: UUID | None = None
    trace_id: str | None = None
    schema_version: int = 1


class AuditPage(BaseModel):
    """A page of audit records with the echo of its pagination cursor."""

    items: list[AuditRecord]
    limit: int
    offset: int
    count: int


@router.get("", response_model=AuditPage)
async def list_audit(
    ctx: Annotated[SecurityContext, Depends(_require_auditor)],
    repo: Annotated[AuditRepository, Depends(get_audit_repo)],
    action: str | None = Query(default=None, description="Filter by AuditEvent action."),
    outcome: str | None = Query(default=None, description="Filter by outcome."),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> AuditPage:
    """Return a tenant-scoped page of audit rows, newest first."""

    if not evaluate(ctx, "DATA_READ", ResourceRef(type="audit")):
        raise ForbiddenError("Not permitted to read the audit log.")

    rows = await repo.list(
        ctx.tenant_id,
        action=action,
        outcome=outcome,
        limit=limit,
        offset=offset,
    )
    items = [
        AuditRecord(
            audit_id=r.audit_id,
            occurred_at=r.occurred_at,
            tenant_id=r.tenant_id,
            actor=r.actor,
            action=r.action,
            resource=r.resource,
            outcome=r.outcome,
            reason=r.reason,
            decision_id=r.decision_id,
            trace_id=r.trace_id,
            schema_version=r.schema_version,
        )
        for r in rows
    ]
    return AuditPage(items=items, limit=limit, offset=offset, count=len(items))
