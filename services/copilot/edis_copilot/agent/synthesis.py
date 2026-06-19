"""Answer assembly + the deterministic OFFLINE agent (real tools, templated answer).

Two responsibilities:

1. :class:`CopilotAnswer` (the turn's structured output) and :func:`finalize_answer` —
   the SHARED grounding+citation finisher both the LLM loop and the offline agent run.
   It builds numbered citations + the ``facts_used`` whitelist from the turn's tool
   results, verifies every number in the draft answer against that whitelist, and on any
   unmatched number STRIPS it (replacing with ``[unverified]``) and lowers confidence.
   (The one re-prompt happens in the LLM loop *before* calling this; by the time we
   finalize, stripping is the last-resort enforcement.)

2. :func:`offline_answer` — the deterministic rule-driven agent. With no Anthropic key,
   it routes by rules, calls the REAL read-only tools (metric_lookup + find_anomalies +
   semantic_search) with the server-side tenant, and templates a grounded, cited answer
   purely from the real retrieved data. It never invents a number (every figure it
   writes comes from a tool result, so it passes its own grounding finisher by
   construction) and never raises into the request.

Pure-ish: depends only on the registry (real tools) + grounding helpers. No SDK, no key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from edis_copilot.grounding import (
    DEFAULT_REL_TOL,
    CitationSet,
    allowed_numbers,
    build_citations,
    strip_ungrounded_numbers,
    verify_answer,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from edis_copilot.agent.router import Route
    from edis_copilot.tools.base import ToolContext, ToolResult
    from edis_copilot.tools.registry import ToolRegistry


@dataclass
class CopilotAnswer:
    """The structured, grounded output of one copilot turn.

    ``answer_text`` is the final (verified, possibly number-stripped) prose.
    ``citations`` + ``facts_used`` are the authoritative provenance the UI renders.
    ``answer_model`` is the LLM model IFF an LLM answer passed the guard, else ``None``
    (the offline path always reports ``None``). ``grounding_passed`` records whether the
    draft was clean *before* stripping; ``confidence`` is lowered when it was not or when
    the loop degraded. ``tool_trace`` records the tools invoked this turn.
    """

    answer_text: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    facts_used: list[float] = field(default_factory=list)
    answer_model: str | None = None
    grounding_passed: bool = True
    confidence: float = 1.0
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    route: dict[str, Any] | None = None
    degraded: bool = False
    degrade_reason: str | None = None
    cache_read_input_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Render the answer as the JSON the SSE ``done`` frame / persistence carries."""

        return {
            "answer": self.answer_text,
            "answer_model": self.answer_model,
            "citations": self.citations,
            "facts_used": self.facts_used,
            "grounding_passed": self.grounding_passed,
            "confidence": self.confidence,
            "tool_trace": self.tool_trace,
            "route": self.route,
            "degraded": self.degraded,
            "degrade_reason": self.degrade_reason,
        }


def finalize_answer(
    draft: str,
    results: list["ToolResult"],
    *,
    answer_model: str | None,
    rel_tol: float = DEFAULT_REL_TOL,
    base_confidence: float = 0.9,
    degraded: bool = False,
    degrade_reason: str | None = None,
    cache_read_input_tokens: int = 0,
    route: dict[str, Any] | None = None,
) -> CopilotAnswer:
    """Build citations, verify grounding, strip ungrounded numbers, and assemble the answer.

    This is the single grounding finisher both agent paths call. If every number in
    ``draft`` traces to a tool result this turn, the answer is accepted as-is. If not,
    the offending numbers are replaced with ``[unverified]``, ``grounding_passed`` is set
    False, and confidence is lowered — never surface an ungrounded figure. A degraded
    turn (capped/refused/budget) also lowers confidence. Appends the deterministic
    citations footer when the draft does not already carry one.
    """

    cites: CitationSet = build_citations(results)
    whitelist = allowed_numbers(results)
    verdict = verify_answer(draft, whitelist, rel_tol=rel_tol)

    text = draft
    grounding_passed = verdict.ok
    confidence = base_confidence
    if not verdict.ok:
        text = strip_ungrounded_numbers(draft, whitelist, rel_tol=rel_tol)
        confidence = min(confidence, 0.5)
    if degraded:
        confidence = min(confidence, 0.4)

    footer = cites.markers_footer()
    if footer and "Citations:" not in text:
        text = f"{text.rstrip()}\n\n{footer}"

    return CopilotAnswer(
        answer_text=text,
        citations=cites.to_dicts(),
        facts_used=list(cites.facts_used),
        answer_model=answer_model,
        grounding_passed=grounding_passed,
        confidence=round(confidence, 3),
        tool_trace=[{"tool": r.tool, "summary": r.summary, "rows": len(r.rows)} for r in results],
        route=route,
        degraded=degraded,
        degrade_reason=degrade_reason,
        cache_read_input_tokens=cache_read_input_tokens,
    )


# ---------------------------------------------------------------------------
# Offline deterministic agent (real tools, templated grounded answer)
# ---------------------------------------------------------------------------
async def offline_answer(
    question: str,
    ctx: "ToolContext",
    *,
    registry: "ToolRegistry",
    route: "Route",
    rel_tol: float = DEFAULT_REL_TOL,
) -> CopilotAnswer:
    """Answer ``question`` with no LLM: rule-route, call the real tools, template a cite.

    Calls the SAME read-only tools the Opus loop would (tenant injected from ``ctx``
    server-side), then composes a grounded narrative purely from the returned rows. Every
    number in the narrative is copied from a tool result, so it is grounded by
    construction; it still runs through :func:`finalize_answer` (defense in depth). Never
    raises — a tool error becomes an empty result, and an out-of-scope route yields a
    safe refusal answer.
    """

    if not route.scope_ok:
        return CopilotAnswer(
            answer_text=(
                "I can only answer read-only questions about this tenant's business "
                "metrics, anomalies, and recommendations — I can't take actions or "
                "access other tenants."
            ),
            answer_model=None,
            grounding_passed=True,
            confidence=1.0,
            route=route.to_dict(),
        )

    from edis_copilot.agent.router import resolve_time_range

    start, end = resolve_time_range(route.time_range)
    iso = lambda d: d.isoformat() if d is not None else None  # noqa: E731
    results: list[ToolResult] = []

    # 1) The headline metric move (revenue is the demo's primary KPI).
    results.append(
        await _safe_call(
            registry,
            "metric_lookup",
            ctx,
            metric_key="revenue",
            rollup="day",
            start=iso(start),
            end=iso(end),
        )
    )
    # 2) The driving anomaly/finding (root cause + candidate causes).
    results.append(
        await _safe_call(
            registry, "find_anomalies", ctx, metric_key="revenue", start=iso(start), end=iso(end)
        )
    )
    # 3) The most relevant recommendation (for "what should we do").
    results.append(await _safe_call(registry, "semantic_search", ctx, query=question, limit=5))

    draft = _render_offline_narrative(question, route, results)
    return finalize_answer(
        draft,
        results,
        answer_model=None,
        rel_tol=rel_tol,
        base_confidence=0.8,  # offline template is grounded but less nuanced than Opus
        route=route.to_dict(),
    )


async def _safe_call(
    registry: "ToolRegistry", name: str, ctx: "ToolContext", **kwargs: Any
) -> "ToolResult":
    """Dispatch a tool, turning any ToolError/exception into an empty result row.

    Keeps the offline agent total: a missing metric or a bad arg yields a zero-row
    result (cited, but contributing no numbers) rather than aborting the turn.
    """

    from edis_copilot.tools.base import ToolResult

    # Drop None kwargs so optional args use the tool's defaults.
    clean = {k: v for k, v in kwargs.items() if v is not None}
    try:
        return await registry.dispatch(name, ctx, **clean)
    except Exception as exc:  # noqa: BLE001 - offline agent must never raise
        return ToolResult(
            tool=name,
            rows=[],
            numbers=[],
            citation=f"tool {name}",
            summary=f"{name}: no result ({type(exc).__name__}).",
        )


def _render_offline_narrative(question: str, route: "Route", results: list["ToolResult"]) -> str:
    """Compose a grounded narrative purely from the tool rows (every number from a tool).

    Ordering mirrors the architecture answer shape: headline metric move, then the most
    likely root cause from the finding's candidate causes, then the recommended action if
    one was retrieved. Numbers are taken verbatim from the rows, so the result passes the
    grounding guard by construction.
    """

    by_tool = {r.tool: r for r in results}
    parts: list[str] = []

    # Headline finding (observed vs expected, deviation_pct) — the strongest signal.
    fa = by_tool.get("find_anomalies")
    finding = fa.rows[0] if fa and fa.rows else None
    if finding:
        dims = finding.get("dimensions") or {}
        scope = ", ".join(f"{k}={v}" for k, v in sorted(dims.items())) or "overall"
        obs = finding.get("observed_value")
        exp = finding.get("expected_value")
        dpct = finding.get("deviation_pct")
        head = f"{finding.get('metric_key', 'the metric')} ({scope})"
        if obs is not None and exp is not None and dpct is not None:
            parts.append(f"{head} moved to {obs} from an expected {exp}, a change of {dpct}%. [1]")
        elif dpct is not None:
            parts.append(f"{head} changed by {dpct}% versus expectation. [1]")
        else:
            parts.append(f"{head} moved outside its expected range. [1]")

        causes = finding.get("candidate_causes") or []
        if causes:
            c = causes[0]
            corr = c.get("correlation")
            lag = c.get("lag_minutes")
            contrib = c.get("contribution_pct")
            bits = [f"the most likely driver is {c.get('metric_key', 'a related metric')}"]
            if contrib is not None:
                bits.append(f"accounting for ~{contrib}% of the attributed impact")
            if corr is not None:
                bits.append(f"correlation {corr}")
            if lag is not None:
                bits.append(f"leading by {lag} minutes")
            parts.append("Root cause: " + ", ".join(bits) + ". [2]")
    else:
        # No finding — fall back to the metric series headline if present.
        ml = by_tool.get("metric_lookup")
        if ml and ml.rows:
            last = ml.rows[-1]
            parts.append(
                f"The latest {last.get('metric_key', 'metric')} reading is "
                f"{last.get('value')}. [1]"
            )
        else:
            parts.append(
                "No anomalies or metric movements were found for this window in the "
                "available data."
            )

    # Recommended action from semantic search.
    ss = by_tool.get("semantic_search")
    rec = None
    if ss:
        for row in ss.rows:
            if row.get("kind") == "recommendation":
                rec = row
                break
    if rec:
        title = (rec.get("payload") or {}).get("title") or rec.get("text") or "a recommended action"
        nums = rec.get("numbers") or []
        if nums:
            parts.append(f"Recommended action: {title} (estimated impact {nums[0]}). [3]")
        else:
            parts.append(f"Recommended action: {title}. [3]")

    if not parts:
        parts.append("I could not find grounded data to answer that question.")
    return " ".join(parts)
