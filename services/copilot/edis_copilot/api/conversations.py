"""``GET /v1/copilot/conversations`` — list the tenant's copilot conversations.

A small read surface over the copilot's own history (``copilot_conversation``). Strictly
tenant-scoped: the tenant comes from the verified principal (gateway-injected header or
JWT), never a query param, so a caller can only ever see their own tenant's threads. The
in-memory answer repository backs this with no DB in the bare app / tests.
"""

from __future__ import annotations

from edis_contracts.security import SecurityContext
from fastapi import APIRouter, Depends, Query

from edis_copilot.deps import get_answer_repository, get_principal

router = APIRouter(tags=["copilot"])


@router.get("/v1/copilot/conversations", summary="List the tenant's copilot conversations")
async def list_conversations(
    principal: SecurityContext = Depends(get_principal),
    answers=Depends(get_answer_repository),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, object]:
    """Return the verified tenant's conversations, newest first (tenant-scoped)."""

    if answers is None:
        return {"tenant_id": principal.tenant_id, "conversations": []}
    rows = await answers.list_conversations(principal.tenant_id, limit=limit)
    return {"tenant_id": principal.tenant_id, "conversations": rows}
