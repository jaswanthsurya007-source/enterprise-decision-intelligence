"""``POST /v1/copilot/chat`` — the streaming, grounded copilot turn (SSE).

This is the single chat entry the gateway proxies to. The gateway is the authoritative
authorization boundary: it validates the browser JWT, enforces the ``AI_QUERY`` RBAC
gate, and forwards the VERIFIED identity as trusted server-side headers
(``X-EDIS-Tenant`` / ``X-EDIS-User`` / ``X-EDIS-Roles``). The copilot therefore derives
the principal — and thus the tenant every tool is scoped to — ONLY from those headers,
never from the request body and never from the LLM. (A direct bearer JWT is also accepted
for non-gateway callers / tests via the platform dep.)

The flow per turn:

1. Resolve the principal (gateway headers, else JWT) -> :class:`ToolContext`.
2. Stream the agent loop (:func:`edis_copilot.agent.loop.answer`) over SSE — token / tool_call /
   citation / usage / done frames. Tenant is injected into every tool from the context.
3. After the answer assembles, persist the grounded :class:`CopilotAnswer` and emit an
   ``AI_QUERY`` audit event (tools invoked, grounding outcome) — best-effort, never
   blocking the stream.

The loop never raises into the request; the SSE driver guards the stream itself. Uses
FastAPI ``StreamingResponse`` (no extra SSE dependency).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from edis_contracts.security import SecurityContext
from edis_platform.logging import get_logger
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from edis_copilot.agent.loop import answer as run_answer_loop
from edis_copilot.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE, run_chat_stream
from edis_copilot.deps import (
    get_answer_repository,
    get_audit_emitter,
    get_budget,
    get_llm,
    get_loop_limits,
    get_principal,
    get_registry,
)
from edis_copilot.tools.base import ToolContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    from edis_copilot.agent.synthesis import CopilotAnswer

_log = get_logger(__name__)

router = APIRouter(tags=["copilot"])


class ChatRequest(BaseModel):
    """The chat request body — just the question (+ optional conversation id).

    The tenant is intentionally ABSENT: it can never be set from the body. ``question``
    is the only model-influenced field; ``conversation_id`` threads multi-turn history.
    """

    model_config = {"extra": "ignore"}

    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None


@router.post("/v1/copilot/chat", summary="Stream a grounded, cited copilot answer (SSE)")
async def copilot_chat(
    request: Request,
    body: ChatRequest,
    principal: SecurityContext = Depends(get_principal),
    registry=Depends(get_registry),
    llm=Depends(get_llm),
    budget=Depends(get_budget),
    limits=Depends(get_loop_limits),
    answers=Depends(get_answer_repository),
    audit=Depends(get_audit_emitter),
) -> StreamingResponse:
    """Run one grounded copilot turn for the verified principal, streamed as SSE.

    Tenant comes only from ``principal`` (gateway-injected or JWT) and is threaded into
    every tool via the :class:`ToolContext`. Persistence + the ``AI_QUERY`` audit event
    fire after the answer assembles, without blocking the token stream.
    """

    ctx = ToolContext(security=principal, trace_id=request.headers.get("X-EDIS-Trace"))
    captured: dict[str, Any] = {}

    async def run_answer(emit) -> "CopilotAnswer":
        result = await run_answer_loop(
            body.question,
            ctx,
            registry=registry,
            llm=llm,
            limits=limits,
            budget=budget,
            emit=emit,
        )
        captured["answer"] = result
        # Side-effects after the answer is assembled (best-effort; never break the stream).
        await _persist_and_audit(
            answers=answers,
            audit=audit,
            principal=principal,
            ctx=ctx,
            question=body.question,
            conversation_id=body.conversation_id,
            result=result,
        )
        return result

    return StreamingResponse(
        run_chat_stream(run_answer, is_disconnected=request.is_disconnected),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )


async def _persist_and_audit(
    *,
    answers,
    audit,
    principal: SecurityContext,
    ctx: ToolContext,
    question: str,
    conversation_id: str | None,
    result: "CopilotAnswer",
) -> None:
    """Persist the answer and emit the ``AI_QUERY`` audit event (both best-effort)."""

    from uuid import UUID

    if answers is not None:
        try:
            conv_uuid = UUID(conversation_id) if conversation_id else None
            await answers.save_answer(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                question=question,
                answer=result,
                conversation_id=conv_uuid,
            )
        except Exception as exc:  # noqa: BLE001 - persistence must not break the turn
            _log.warning("copilot answer persist failed", extra={"error": str(exc)})

    if audit is not None:
        try:
            await audit.emit(
                principal,
                "AI_QUERY",
                {
                    "type": "copilot",
                    "id": "chat",
                    "tools": [t.get("tool") for t in result.tool_trace],
                    "grounding_passed": result.grounding_passed,
                    "answer_model": result.answer_model,
                },
                "ALLOW",
                reason=result.degrade_reason,
                trace_id=ctx.trace_id,
            )
        except Exception as exc:  # noqa: BLE001 - audit is fire-and-forget
            _log.warning("copilot AI_QUERY audit failed", extra={"error": str(exc)})
