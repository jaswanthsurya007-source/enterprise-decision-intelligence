"""P3 — the manual Opus tool-use loop driven by a scripted FakeLLM (no network, no key).

The FakeLLM is a scripted ``AsyncAnthropic`` stand-in: it routes the question (via
``messages.parse``), emits ``tool_use`` blocks across iterations, then a final grounded
``end_turn`` answer. These tests drive the real loop in ``app.agent.loop.answer`` through
the full demo trajectory — route -> find_anomalies + metric_lookup + semantic_search ->
grounded final answer — and verify every ``stop_reason`` transition the loop must handle
(``tool_use`` -> ``pause_turn`` -> ``end_turn``, plus ``refusal`` / ``max_tokens``
degrade). Nothing hits the network; the tools run against the seeded InMemory port.
"""

from __future__ import annotations

from edis_copilot.agent import LoopLimits, answer
from cp_testkit import assistant_turn, FakeLLM, text_block, tool_use_block


async def test_loop_routes_then_runs_three_tools_then_grounded_answer(registry, ctx):
    """route -> find_anomalies + metric_lookup + semantic_search -> a grounded final answer."""

    llm = FakeLLM(
        [
            # Iteration 1: the model asks for the driving anomaly.
            assistant_turn(
                stop_reason="tool_use",
                content=[
                    text_block("Let me look at the anomaly and the series."),
                    tool_use_block("find_anomalies", {"metric_key": "revenue"}, id="t1"),
                ],
            ),
            # Iteration 2: the model asks for the metric series + the recommendation.
            assistant_turn(
                stop_reason="tool_use",
                content=[
                    tool_use_block(
                        "metric_lookup",
                        {
                            "metric_key": "revenue",
                            "rollup": "day",
                            "dimensions": {"region": "EMEA", "channel": "web"},
                        },
                        id="t2",
                    ),
                    tool_use_block(
                        "semantic_search",
                        {"query": "what should we do about the EMEA revenue drop"},
                        id="t3",
                    ),
                ],
            ),
            # Iteration 3: the grounded final answer, every number from the tool results.
            assistant_turn(
                stop_reason="end_turn",
                content=[
                    text_block(
                        "EMEA web revenue fell to 61000 from an expected 95000, a 35.8% drop, "
                        "with checkout-api latency the leading driver (correlation 0.94). [1] "
                        "Recommended action recovers an estimated 170000. [3]"
                    )
                ],
            ),
        ]
    )

    result = await answer(
        "Why did revenue drop last week and what should we do?",
        ctx,
        registry=registry,
        llm=llm,
    )

    assert result.answer_model == "claude-opus-4-8"
    assert result.grounding_passed is True
    assert "[unverified]" not in result.answer_text
    # Routing happened via the FakeLLM's parse path (haiku route).
    assert result.route["source"] == "haiku"
    assert llm.parse_calls, "the router should have called messages.parse"
    # All three tools actually dispatched against the seeded data.
    tools_run = {t["tool"] for t in result.tool_trace}
    assert {"find_anomalies", "metric_lookup", "semantic_search"} <= tools_run
    # Three streamed iterations (two tool turns + the final answer).
    assert len(llm.stream_calls) == 3
    assert result.citations


async def test_loop_tenant_injected_server_side_even_when_model_supplies_one(registry, ctx):
    """A ``tenant_id`` in the model's tool_use input is stripped; ctx tenant is used."""

    llm = FakeLLM(
        [
            assistant_turn(
                stop_reason="tool_use",
                content=[
                    tool_use_block(
                        "find_anomalies", {"metric_key": "revenue", "tenant_id": "globex"}, id="t1"
                    )
                ],
            ),
            assistant_turn(
                stop_reason="end_turn",
                content=[text_block("Observed 61000 versus expected 95000. [1]")],
            ),
        ]
    )
    result = await answer("why did revenue drop", ctx, registry=registry, llm=llm)
    # acme's finding (61000) surfaced — never globex's lone 999999 point.
    assert any(abs(n - 61000.0) < 1.0 for n in result.facts_used)
    assert all(abs(n - 999999.0) > 1.0 for n in result.facts_used)


async def test_loop_handles_pause_turn_then_end_turn(registry, ctx):
    """``pause_turn`` re-sends (assistant turn appended, no new user turn) then completes."""

    llm = FakeLLM(
        [
            assistant_turn(stop_reason="pause_turn", content=[text_block("thinking…")]),
            assistant_turn(stop_reason="end_turn", content=[text_block("All nominal.")]),
        ]
    )
    result = await answer("status", ctx, registry=registry, llm=llm)
    assert result.degraded is False
    assert "nominal" in result.answer_text.lower()
    assert len(llm.stream_calls) == 2  # paused, then resumed


async def test_loop_refusal_degrades_to_grounded_synthesis(registry, ctx):
    """A ``refusal`` stop_reason degrades to a grounded answer from collected tool results."""

    llm = FakeLLM(
        [
            assistant_turn(
                stop_reason="tool_use",
                content=[tool_use_block("find_anomalies", {"metric_key": "revenue"}, id="t1")],
            ),
            assistant_turn(stop_reason="refusal", content=[]),
        ]
    )
    result = await answer("why did revenue drop", ctx, registry=registry, llm=llm)
    assert result.degraded is True
    assert result.degrade_reason.startswith("refusal")
    # Still grounded — synthesized from the real find_anomalies result, not invented.
    assert result.grounding_passed is True
    assert result.answer_model is None


async def test_loop_iteration_cap_prevents_runaway(registry, ctx):
    """A model that always asks for a tool hits the cap and degrades (no infinite loop)."""

    forever = [
        assistant_turn(
            stop_reason="tool_use",
            content=[tool_use_block("find_anomalies", {"metric_key": "revenue"}, id=f"t{i}")],
        )
        for i in range(10)
    ]
    result = await answer(
        "why", ctx, registry=registry, llm=FakeLLM(forever), limits=LoopLimits(max_iterations=3)
    )
    assert result.degraded is True
    assert result.degrade_reason == "max_iterations"
