"""The manual Claude tool-use loop + the single ``answer(...)`` entrypoint.

This is the heart of L5. :func:`answer` is the ONE entrypoint both the API and the tests
call: ``answer(question, ctx, *, data, llm) -> CopilotAnswer``. It routes the question
(Haiku or rules), then runs either:

* the **manual Opus tool-use loop** (when an Anthropic client is present), or
* the **deterministic offline agent** (no key) — :func:`edis_copilot.agent.synthesis.offline_answer`.

THE MANUAL LOOP (verified against the Claude API reference; do not deviate):

* Per iteration, stream opus-4-8 with adaptive thinking (``display:"summarized"``),
  ``output_config={"effort":"high"}``, the CACHED system blocks, the FROZEN tool
  schemas, and the running ``messages``; then ``msg = await stream.get_final_message()``.
* Branch on ``msg.stop_reason`` BEFORE reading content:
  - ``end_turn`` -> done (synthesize the final answer).
  - ``tool_use`` -> run each tool (tenant injected from ``ctx`` server-side, NEVER from
    the model), append ``{"role":"assistant","content":msg.content}`` then
    ``{"role":"user","content":[tool_result, ...]}``, loop.
  - ``pause_turn`` -> re-send to continue (append the assistant turn, no new user turn).
  - ``refusal`` / ``max_tokens`` -> stop + degrade (fall back to a grounded offline
    synthesis from whatever tool results we have).
* NO temperature/top_p/budget_tokens. Enforce a max-iteration cap and a per-tenant
  budget (priced via ``messages.count_tokens`` before each call).
* Prompt caching: the frozen tools + system prefix are stable; ``usage.cache_read_input_tokens``
  is surfaced on the answer (asserted > 0 after warm-up by the tests).

GROUNDING: after the final answer, every numeric claim is checked against the per-turn
tool-result whitelist; an unmatched number triggers ONE re-prompt, then the number is
stripped and confidence lowered (in :func:`edis_copilot.agent.synthesis.finalize_answer`).

The loop emits SSE frames through an optional ``emit`` async callback (token / tool_call
/ citation / usage / done) so the API can stream; with no callback it just returns the
:class:`~app.agent.synthesis.CopilotAnswer`. It NEVER raises into the request — every
failure path degrades to a grounded answer.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from edis_platform.logging import get_logger

from edis_copilot.agent.limits import LoopLimits, LoopState, MaxIterationsReached
from edis_copilot.agent.router import Route, route_question
from edis_copilot.agent.synthesis import CopilotAnswer, finalize_answer, offline_answer
from edis_copilot.budget.accounting import (
    BudgetAccountant,
    BudgetExceeded,
    CostModel,
    count_request_tokens,
)
from edis_copilot.grounding import DEFAULT_REL_TOL, allowed_numbers, verify_answer
from edis_copilot.llm.models import MODEL_OPUS, opus_request_kwargs
from edis_copilot.llm.prompts import system_blocks
from edis_copilot.telemetry.tracing import span
from edis_copilot.tools.base import ToolError, ToolResult

if TYPE_CHECKING:  # pragma: no cover - typing only
    from edis_copilot.tools.base import ToolContext
    from edis_copilot.tools.registry import ToolRegistry

_log = get_logger(__name__)

#: An emit callback receives one frame dict; the API turns it into an SSE event.
EmitFn = Callable[[dict[str, Any]], Awaitable[None]]


async def answer(
    question: str,
    ctx: "ToolContext",
    *,
    registry: "ToolRegistry",
    llm: Any | None = None,
    limits: LoopLimits | None = None,
    budget: BudgetAccountant | None = None,
    rel_tol: float = DEFAULT_REL_TOL,
    emit: EmitFn | None = None,
) -> CopilotAnswer:
    """Answer ``question`` for ``ctx``'s tenant, grounded and cited. The ONE entrypoint.

    ``registry`` is the frozen read-only tool registry; ``llm`` is an ``AsyncAnthropic``
    client or ``None`` (offline). ``budget`` enforces the per-tenant daily cap (a fresh,
    cap-disabled accountant is used if omitted). ``emit`` streams SSE frames when present.
    Returns a :class:`CopilotAnswer`; never raises into the caller.
    """

    limits = limits or LoopLimits()
    budget = budget or BudgetAccountant(cap_usd=0.0)

    route = await route_question(question, client=llm)
    if emit is not None:
        await emit({"type": "route", "route": route.to_dict()})

    if llm is None:
        result = await offline_answer(
            question, ctx, registry=registry, route=route, rel_tol=rel_tol
        )
        await _emit_answer_frames(emit, result)
        return result

    try:
        result = await _run_llm_loop(
            question,
            ctx,
            registry=registry,
            llm=llm,
            route=route,
            limits=limits,
            budget=budget,
            rel_tol=rel_tol,
            emit=emit,
        )
    except Exception as exc:  # noqa: BLE001 - the loop must never raise into the request
        _log.warning(
            "copilot LLM loop failed; degrading to offline synthesis",
            extra={"tenant_id": ctx.tenant_id, "error": str(exc), "error_type": type(exc).__name__},
        )
        result = await offline_answer(
            question, ctx, registry=registry, route=route, rel_tol=rel_tol
        )
        result.degraded = True
        result.degrade_reason = result.degrade_reason or f"loop_error:{type(exc).__name__}"
    await _emit_answer_frames(emit, result)
    return result


# ---------------------------------------------------------------------------
# The manual Opus tool-use loop
# ---------------------------------------------------------------------------
async def _run_llm_loop(
    question: str,
    ctx: "ToolContext",
    *,
    registry: "ToolRegistry",
    llm: Any,
    route: Route,
    limits: LoopLimits,
    budget: BudgetAccountant,
    rel_tol: float,
    emit: EmitFn | None,
) -> CopilotAnswer:
    """Run the manual streaming tool-use loop until end_turn / a degrade condition."""

    tools = registry.anthropic_tools()
    sys_blocks = system_blocks()
    messages: list[dict[str, Any]] = [{"role": "user", "content": _user_turn(question, route)}]
    state = LoopState()
    final_text = ""

    while True:
        try:
            iteration = state.next_iteration(limits)
        except MaxIterationsReached:
            state.mark_degraded("max_iterations")
            break

        # Budget guard: price this call and check the per-tenant cap before sending.
        try:
            projected = await _project_cost(llm, sys_blocks, tools, messages, limits)
            await budget.check(ctx.tenant_id, projected_usd=projected)
        except BudgetExceeded as exc:
            _log.warning("copilot budget exceeded; degrading", extra={"tenant_id": ctx.tenant_id})
            state.mark_degraded(f"budget_exceeded:${exc.cap_usd:.2f}")
            break

        async with span(
            "iteration",
            tenant_id=ctx.tenant_id,
            trace_id=ctx.trace_id,
            attributes={"iteration": iteration},
        ):
            msg = await _stream_once(llm, sys_blocks, tools, messages, limits, emit, budget, ctx)

        stop_reason = getattr(msg, "stop_reason", None)
        state.cache_read_input_tokens = max(state.cache_read_input_tokens, _cache_read(msg))

        if stop_reason == "refusal":
            state.mark_degraded(f"refusal:{_refusal_category(msg)}")
            break
        if stop_reason == "max_tokens":
            state.mark_degraded("max_tokens")
            break
        if stop_reason == "pause_turn":
            # Server-side pause: re-send with the assistant turn appended, no new user turn.
            messages.append({"role": "assistant", "content": msg.content})
            continue
        if stop_reason == "tool_use":
            tool_uses = [b for b in (msg.content or []) if getattr(b, "type", None) == "tool_use"]
            messages.append({"role": "assistant", "content": msg.content})
            tool_results_content = await _run_tool_uses(
                tool_uses, ctx, registry=registry, state=state, emit=emit
            )
            messages.append({"role": "user", "content": tool_results_content})
            continue

        # end_turn (or any other clean stop): collect the final text and finish.
        final_text = _extract_text(msg)
        break

    return await _finish(
        final_text,
        state,
        llm=llm,
        sys_blocks=sys_blocks,
        tools=tools,
        messages=messages,
        ctx=ctx,
        route=route,
        rel_tol=rel_tol,
        budget=budget,
        limits=limits,
        emit=emit,
    )


async def _stream_once(
    llm: Any,
    sys_blocks: list[dict],
    tools: list[dict],
    messages: list[dict],
    limits: LoopLimits,
    emit: EmitFn | None,
    budget: BudgetAccountant,
    ctx: "ToolContext",
) -> Any:
    """One streamed Opus call; emit token frames as text deltas arrive; record usage.

    Uses the verified streaming shape (``messages.stream`` -> ``get_final_message``).
    Token frames are emitted from ``text`` deltas only (thinking is summarized internally
    and not streamed as answer tokens). Realized usage is recorded to the budget ledger.
    """

    kwargs = opus_request_kwargs(max_tokens=limits.max_output_tokens)
    async with llm.messages.stream(
        system=sys_blocks, tools=tools, messages=messages, **kwargs
    ) as stream:
        if emit is not None:
            # Stream answer-text deltas to the client as they arrive.
            async for event in stream:
                if getattr(event, "type", None) == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if getattr(delta, "type", None) == "text_delta":
                        await emit({"type": "token", "text": getattr(delta, "text", "") or ""})
        msg = await stream.get_final_message()

    usage = _usage_dict(msg)
    if usage:
        await budget.record(ctx.tenant_id, CostModel.usd_from_usage(MODEL_OPUS, usage))
        if emit is not None:
            await emit({"type": "usage", "model": MODEL_OPUS, "usage": usage})
    return msg


async def _run_tool_uses(
    tool_uses: list[Any],
    ctx: "ToolContext",
    *,
    registry: "ToolRegistry",
    state: LoopState,
    emit: EmitFn | None,
) -> list[dict[str, Any]]:
    """Run each requested tool with the SERVER-SIDE tenant; return tool_result blocks.

    The tenant comes only from ``ctx`` (never from ``block.input``). A :class:`ToolError`
    becomes an ``is_error`` tool_result so the model can adapt; the accumulated
    :class:`ToolResult` (for citations/grounding) is appended only for successful calls.
    """

    out: list[dict[str, Any]] = []
    for block in tool_uses:
        name = getattr(block, "name", "")
        tool_use_id = getattr(block, "id", "")
        tool_input = _block_input(block)
        if emit is not None:
            await emit({"type": "tool_call", "tool": name, "input": tool_input})
        async with span(
            "tool",
            tenant_id=ctx.tenant_id,
            trace_id=ctx.trace_id,
            attributes={"tool": name, "input": tool_input},
        ):
            try:
                # Strip any tenant the model tried to smuggle in; tenant is server-side.
                safe_input = {
                    k: v for k, v in tool_input.items() if k not in ("tenant_id", "tenant")
                }
                result: ToolResult = await registry.dispatch(name, ctx, **safe_input)
            except ToolError as exc:
                out.append(_tool_result_block(tool_use_id, {"error": str(exc)}, is_error=True))
                state.tool_calls.append({"tool": name, "ok": False, "error": str(exc)})
                continue
            except Exception as exc:  # noqa: BLE001 - unexpected tool failure: tell the model
                out.append(_tool_result_block(tool_use_id, {"error": "tool failed"}, is_error=True))
                state.tool_calls.append({"tool": name, "ok": False, "error": type(exc).__name__})
                continue

        state.results.append(result)
        state.tool_calls.append({"tool": name, "ok": True, "rows": len(result.rows)})
        out.append(_tool_result_block(tool_use_id, result.to_tool_content()))
    return out


async def _finish(
    final_text: str,
    state: LoopState,
    *,
    llm: Any,
    sys_blocks: list[dict],
    tools: list[dict],
    messages: list[dict],
    ctx: "ToolContext",
    route: Route,
    rel_tol: float,
    budget: BudgetAccountant,
    limits: LoopLimits,
    emit: EmitFn | None,
) -> CopilotAnswer:
    """Verify grounding; on an unmatched number do ONE re-prompt, then finalize.

    If the draft answer has an ungrounded number and the turn is not already degraded,
    we re-prompt the model once (a user turn instructing it to remove/replace any figure
    not in the tool results) and re-stream. The final :func:`finalize_answer` then strips
    anything still unmatched and lowers confidence. If the loop degraded with no usable
    text, we synthesize a grounded answer from the accumulated tool results.
    """

    whitelist = allowed_numbers(state.results)

    # Degraded with no usable text -> grounded offline synthesis from collected results.
    if state.degraded and not final_text.strip():
        from edis_copilot.agent.synthesis import _render_offline_narrative  # local: shared template

        draft = _render_offline_narrative("", route, state.results)
        return finalize_answer(
            draft,
            state.results,
            answer_model=None,
            rel_tol=rel_tol,
            base_confidence=0.6,
            degraded=True,
            degrade_reason=state.degrade_reason,
            cache_read_input_tokens=state.cache_read_input_tokens,
            route=route.to_dict(),
        )

    # One grounding re-prompt before stripping (only when not degraded and we can call).
    verdict = verify_answer(final_text, whitelist, rel_tol=rel_tol)
    if not verdict.ok and not state.degraded and state.iteration < limits.max_iterations:
        messages.append({"role": "assistant", "content": [{"type": "text", "text": final_text}]})
        messages.append({"role": "user", "content": _reprompt_turn(verdict.unmatched)})
        try:
            await budget.check(ctx.tenant_id)
            async with span("reprompt", tenant_id=ctx.tenant_id, trace_id=ctx.trace_id):
                msg = await _stream_once(
                    llm, sys_blocks, tools, messages, limits, emit, budget, ctx
                )
            if getattr(msg, "stop_reason", None) not in ("refusal", "max_tokens"):
                retext = _extract_text(msg)
                if retext.strip():
                    final_text = retext
            state.cache_read_input_tokens = max(state.cache_read_input_tokens, _cache_read(msg))
        except Exception as exc:  # noqa: BLE001 - re-prompt is best-effort (incl. BudgetExceeded)
            _log.info("grounding re-prompt skipped", extra={"reason": type(exc).__name__})

    return finalize_answer(
        final_text,
        state.results,
        answer_model=MODEL_OPUS if not state.degraded else None,
        rel_tol=rel_tol,
        base_confidence=0.9,
        degraded=state.degraded,
        degrade_reason=state.degrade_reason,
        cache_read_input_tokens=state.cache_read_input_tokens,
        route=route.to_dict(),
    )


# ---------------------------------------------------------------------------
# SSE frame emission for the assembled answer
# ---------------------------------------------------------------------------
async def _emit_answer_frames(emit: EmitFn | None, result: CopilotAnswer) -> None:
    """Emit citation frames + the terminal ``done`` frame for an assembled answer."""

    if emit is None:
        return
    for c in result.citations:
        await emit({"type": "citation", "citation": c})
    await emit({"type": "done", **result.to_dict()})


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def _user_turn(question: str, route: Route) -> str:
    """Build the first user turn — the question plus the (advisory) route hint."""

    return (
        f"Question: {question}\n\n"
        f"(Routing hint — intent={route.intent}, time_range={route.time_range}. "
        "Use the tools to retrieve the facts you need; cite every figure.)"
    )


def _reprompt_turn(unmatched: tuple[float, ...]) -> str:
    """Build the single grounding re-prompt user turn listing the ungrounded figures."""

    nums = ", ".join(str(n) for n in unmatched)
    return (
        "Your previous answer included one or more numbers that do NOT appear in any "
        f"tool result from this turn: {nums}. Rewrite the answer using ONLY figures "
        "returned by the tools. If you need a figure you do not have, call the tool that "
        "returns it or state the point qualitatively without a number."
    )


def _block_input(block: Any) -> dict[str, Any]:
    """Extract a tool_use block's input as a plain dict (parsed JSON, never raw string)."""

    raw = getattr(block, "input", {})
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _tool_result_block(tool_use_id: str, content: Any, *, is_error: bool = False) -> dict[str, Any]:
    """Build a ``tool_result`` content block (JSON-string body, matched id)."""

    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": json.dumps(content, default=str),
    }
    if is_error:
        block["is_error"] = True
    return block


def _extract_text(msg: Any) -> str:
    """Concatenate text from ``msg.content`` blocks where ``block.type == 'text'``."""

    parts: list[str] = []
    for block in getattr(msg, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts).strip()


def _usage_dict(msg: Any) -> dict[str, int]:
    """Pull the usage counters off a message into a plain dict (empty if absent)."""

    usage = getattr(msg, "usage", None)
    if usage is None:
        return {}
    out: dict[str, int] = {}
    for k in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        v = getattr(usage, k, None)
        if v is not None:
            out[k] = int(v)
    return out


def _cache_read(msg: Any) -> int:
    """Cache-read input tokens off a message (the prompt-cache metric); 0 if absent."""

    return int(_usage_dict(msg).get("cache_read_input_tokens", 0))


def _refusal_category(msg: Any) -> str | None:
    details = getattr(msg, "stop_details", None)
    return getattr(details, "category", None) if details is not None else None


async def _project_cost(
    llm: Any, sys_blocks: list[dict], tools: list[dict], messages: list[dict], limits: LoopLimits
) -> float:
    """Project the USD cost of the next Opus call (count_tokens input + max output ceiling)."""

    in_tok = await count_request_tokens(
        llm, model=MODEL_OPUS, system=sys_blocks, tools=tools, messages=messages
    )
    # Conservative: assume the call could emit up to max_output_tokens.
    return CostModel.usd(MODEL_OPUS, input_tokens=in_tok, output_tokens=limits.max_output_tokens)
