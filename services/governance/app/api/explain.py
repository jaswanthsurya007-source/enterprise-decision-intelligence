"""Explainability store API.

``POST /v1/explain/decisions`` is the target of
:class:`edis_gov_sdk.explain.ExplainabilityClient.write_decision` -- L3/L4/L5 POST
a :class:`~edis_contracts.governance.Decision` (with immutable evidence
snapshots) and the governance service persists it. The write is idempotent on
``decision_id`` so a retry is safe.

``GET /v1/explain/decisions/{decision_id}`` reads one back (auditor/admin), used
by "where did this number come from" UIs. The decision is committed here (the
caller -- the SDK over HTTP -- expects durability before its own response).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from edis_contracts.governance import Decision
from edis_contracts.security import ResourceRef, SecurityContext
from edis_platform.authz.rbac import evaluate
from edis_platform.errors import ForbiddenError, NotFoundError
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import DbSession, get_security_context, require_role
from app.repo import ExplainRepository

router = APIRouter(prefix="/v1/explain", tags=["explain"])

_require_auditor = require_role("auditor")


@router.post("/decisions", status_code=status.HTTP_201_CREATED)
async def write_decision(
    decision: Decision,
    ctx: Annotated[SecurityContext, Depends(get_security_context)],
    session: Annotated[AsyncSession, Depends(DbSession)],
    response: Response,
) -> dict:
    """Persist a :class:`Decision` + evidence; idempotent on ``decision_id``.

    The decision's ``tenant_id`` is forced to the caller's verified tenant so a
    token can never write another tenant's explainability record. A producer must
    be permitted to write an AI decision (operator/analyst via ``AI_DECISION`` or
    admin).
    """

    if not evaluate(ctx, "AI_DECISION", ResourceRef(type="decision")):
        raise ForbiddenError("Not permitted to write explainability decisions.")

    # Tenant comes only from the verified token, never the request body.
    scoped = decision.model_copy(update={"tenant_id": ctx.tenant_id})

    repo = ExplainRepository(session)
    created = await repo.write_decision(scoped)
    await session.commit()

    if not created:
        response.status_code = status.HTTP_200_OK
    return {"decision_id": str(scoped.decision_id), "created": created}


@router.get("/decisions/{decision_id}", response_model=Decision)
async def get_decision(
    decision_id: UUID,
    ctx: Annotated[SecurityContext, Depends(_require_auditor)],
    session: Annotated[AsyncSession, Depends(DbSession)],
) -> Decision:
    """Return one explainability :class:`Decision` (tenant-scoped)."""

    if not evaluate(ctx, "DATA_READ", ResourceRef(type="decision")):
        raise ForbiddenError("Not permitted to read explainability decisions.")

    repo = ExplainRepository(session)
    decision = await repo.get_decision(ctx.tenant_id, decision_id)
    if decision is None:
        raise NotFoundError(f"Decision {decision_id} not found.")
    return decision
