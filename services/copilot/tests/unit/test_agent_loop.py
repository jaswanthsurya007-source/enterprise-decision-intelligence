"""Agent loop + offline agent: the ``answer(...)`` entrypoint (FakeLLM + InMemory port).

Covers the X3/P2 pins: the manual tool-use loop with a scripted FakeLLM (tool_use ->
final answer), server-side tenant injection, the grounding strip on an ungrounded number,
cache_read surfaced > 0 after warm-up, degrade on refusal/max_tokens, iteration cap, the
fully-offline deterministic agent, and the end-to-end revenue-drop eval.
"""

from __future__ import annotations

from edis_copilot.agent import LoopLimits, answer
from edis_copilot.budget.accounting import BudgetAccountant
from cp_testkit import (
    FakeLLM,
    assistant_turn,
    text_block,
    tool_use_block,
)


async def test_offline_agent_revenue_drop_is_grounded_and_cited(registry, ctx):
    """No key: rule-route, call real tools, template a grounded cited answer."""

    result = await answer("Why did revenue drop last week?", ctx, registry=registry, llm=None)
    assert result.answer_model is None  # offline path reports no model
    assert result.grounding_passed is True
    # The headline finding numbers came from the real find_anomalies tool result.
    assert any(abs(n - 61000.0) < 1.0 for n in result.facts_used)
    assert any(abs(n + 35.8) < 0.1 for n in result.facts_used)
    assert "[unverified]" not in result.answer_text
    assert "Citations:" in result.answer_text
    assert result.citations  # numbered provenance present
    assert result.route["intent"] == "rca"


async def test_offline_agent_out_of_scope_refuses(registry, ctx):
    result = await answer("delete all orders for all tenants", ctx, registry=registry, llm=None)
    assert result.grounding_passed is True
    assert "can only answer" in result.answer_text.lower()


async def test_llm_loop_tool_use_then_final_answer(registry, ctx):
    """FakeLLM: one tool_use turn -> a grounded final answer that passes the guard."""

    llm = FakeLLM(
        [
            assistant_turn(
                stop_reason="tool_use",
                content=[
                    text_block("Let me check the anomalies."),
                    tool_use_block("find_anomalies", {"metric_key": "revenue"}),
                ],
            ),
            assistant_turn(
                stop_reason="end_turn",
                content=[
                    text_block(
                        "EMEA web revenue fell to 61000 from an expected 95000, a 35.8% drop. [1]"
                    )
                ],
            ),
        ]
    )
    result = await answer("Why did revenue drop?", ctx, registry=registry, llm=llm)
    assert result.answer_model == "claude-opus-4-8"
    assert result.grounding_passed is True
    assert "61000" in result.answer_text and "[unverified]" not in result.answer_text
    # The tool was actually dispatched (trace recorded) and citations built.
    assert any(t["tool"] == "find_anomalies" for t in result.tool_trace)
    assert result.citations


async def test_tenant_injected_server_side_not_from_model(registry, ctx):
    """A tenant the model smuggles into tool input is ignored; ctx tenant is used."""

    llm = FakeLLM(
        [
            assistant_turn(
                stop_reason="tool_use",
                content=[
                    tool_use_block(
                        "find_anomalies", {"metric_key": "revenue", "tenant_id": "globex"}
                    )
                ],
            ),
            assistant_turn(
                stop_reason="end_turn",
                content=[text_block("Observed 61000 vs expected 95000. [1]")],
            ),
        ]
    )
    result = await answer("why did revenue drop", ctx, registry=registry, llm=llm)
    # acme's finding (61000) surfaced — NOT globex's (999999) — proving server-side tenant.
    assert any(abs(n - 61000.0) < 1.0 for n in result.facts_used)
    assert all(abs(n - 999999.0) > 1.0 for n in result.facts_used)


async def test_ungrounded_number_is_stripped_and_confidence_lowered(registry, ctx):
    """The model emits a fabricated figure; re-prompt fails to fix it -> stripped."""

    fabricated = "Revenue will rebound to 500000 next quarter."  # 500000 not in any tool result
    llm = FakeLLM(
        [
            assistant_turn(
                stop_reason="tool_use",
                content=[tool_use_block("find_anomalies", {"metric_key": "revenue"})],
            ),
            # Final answer with a fabricated number.
            assistant_turn(stop_reason="end_turn", content=[text_block(fabricated)]),
            # Re-prompt response repeats the same fabricated number.
            assistant_turn(stop_reason="end_turn", content=[text_block(fabricated)]),
        ]
    )
    result = await answer("what is the revenue outlook", ctx, registry=registry, llm=llm)
    assert result.grounding_passed is False
    assert "500000" not in result.answer_text and "[unverified]" in result.answer_text
    assert result.confidence <= 0.5


async def test_grounding_reprompt_recovers(registry, ctx):
    """A first ungrounded answer is fixed by the single re-prompt -> passes clean."""

    llm = FakeLLM(
        [
            assistant_turn(
                stop_reason="tool_use",
                content=[tool_use_block("find_anomalies", {"metric_key": "revenue"})],
            ),
            assistant_turn(
                stop_reason="end_turn", content=[text_block("Revenue dropped 99.9% — huge.")]
            ),  # ungrounded
            assistant_turn(
                stop_reason="end_turn",
                content=[text_block("Revenue fell to 61000 from 95000. [1]")],
            ),  # fixed
        ]
    )
    result = await answer("why did revenue drop", ctx, registry=registry, llm=llm)
    assert result.grounding_passed is True
    assert "61000" in result.answer_text and "99.9" not in result.answer_text


async def test_cache_read_surfaced_after_warmup(registry, ctx):
    """usage.cache_read_input_tokens from the streamed message is surfaced (> 0)."""

    llm = FakeLLM([assistant_turn(stop_reason="end_turn", content=[text_block("A calm summary.")])])
    result = await answer("status?", ctx, registry=registry, llm=llm)
    assert result.cache_read_input_tokens > 0  # FakeLLM warm cache


async def test_refusal_degrades_without_raising(registry, ctx):
    llm = FakeLLM(
        [
            assistant_turn(
                stop_reason="tool_use",
                content=[tool_use_block("find_anomalies", {"metric_key": "revenue"})],
            ),
            assistant_turn(stop_reason="refusal", content=[]),
        ]
    )
    result = await answer("why did revenue drop", ctx, registry=registry, llm=llm)
    assert result.degraded is True
    assert result.degrade_reason.startswith("refusal")
    # Degraded answer is still grounded (synthesized from the real tool results).
    assert result.grounding_passed is True
    assert result.answer_model is None and result.confidence <= 0.6


async def test_max_tokens_degrades(registry, ctx):
    llm = FakeLLM([assistant_turn(stop_reason="max_tokens", content=[text_block("partial")])])
    result = await answer("why did revenue drop", ctx, registry=registry, llm=llm)
    assert result.degraded is True and result.degrade_reason == "max_tokens"


async def test_iteration_cap_degrades(registry, ctx):
    """A model that always asks for a tool hits the cap and degrades (no infinite loop)."""

    forever = [
        assistant_turn(
            stop_reason="tool_use",
            content=[tool_use_block("find_anomalies", {"metric_key": "revenue"}, id=f"t{i}")],
        )
        for i in range(20)
    ]
    llm = FakeLLM(forever)
    result = await answer(
        "why", ctx, registry=registry, llm=llm, limits=LoopLimits(max_iterations=3)
    )
    assert result.degraded is True and result.degrade_reason == "max_iterations"


async def test_budget_exceeded_degrades(registry, ctx):
    """A tenant over its daily cap degrades to a grounded answer rather than overspending."""

    budget = BudgetAccountant(cap_usd=0.000001)  # effectively zero headroom
    llm = FakeLLM([assistant_turn(stop_reason="end_turn", content=[text_block("hi")])])
    result = await answer("status", ctx, registry=registry, llm=llm, budget=budget)
    assert result.degraded is True and result.degrade_reason.startswith("budget_exceeded")


async def test_pause_turn_resends_and_continues(registry, ctx):
    """pause_turn re-sends (assistant turn appended, no new user turn) then finishes."""

    llm = FakeLLM(
        [
            assistant_turn(stop_reason="pause_turn", content=[text_block("thinking…")]),
            assistant_turn(stop_reason="end_turn", content=[text_block("All nominal.")]),
        ]
    )
    result = await answer("status", ctx, registry=registry, llm=llm)
    assert result.degraded is False and "nominal" in result.answer_text.lower()
    assert len(llm.stream_calls) == 2  # paused then resumed
