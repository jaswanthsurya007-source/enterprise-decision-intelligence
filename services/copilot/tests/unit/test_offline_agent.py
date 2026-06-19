"""P3 — the fully-offline deterministic agent (no key): real tools, templated cited answer.

With NO Anthropic key the whole copilot runs the deterministic rule-driven agent. It still
routes the question, calls the REAL read-only tools (tenant injected server-side), and
templates a grounded, CITED answer purely from the retrieved rows — it never invents a
number. These tests run the seeded demo ("Why did revenue drop last week?") and assert the
narrative carries the actual figures the tools returned (the EMEA revenue-drop shape:
61000 vs 95000, a -35.8% deviation, the checkout-api latency driver), every one traceable
to a tool result — never fabricated.
"""

from __future__ import annotations

from edis_copilot.agent.loop import answer
from edis_copilot.grounding import allowed_numbers, extract_numbers


async def test_offline_answer_is_grounded_cited_and_uses_real_figures(registry, ctx):
    """No key: a templated, cited answer whose figures all come from the real tools."""

    result = await answer("Why did revenue drop last week?", ctx, registry=registry, llm=None)

    # Offline path: no model reported, routed as a root-cause question.
    assert result.answer_model is None
    assert result.route["intent"] == "rca"
    assert result.degraded is False

    # The headline figures are the REAL seeded finding numbers (not invented).
    assert any(abs(n - 61000.0) < 1.0 for n in result.facts_used)  # observed
    assert any(abs(n - 95000.0) < 1.0 for n in result.facts_used)  # expected
    assert any(abs(n + 35.8) < 0.1 for n in result.facts_used)  # -35.8% deviation
    assert any(abs(n - 1220.0) < 1.0 for n in result.facts_used)  # checkout-api latency delta

    # Cited + grounded: every number in the prose traces to a tool result; nothing stripped.
    assert result.grounding_passed is True
    assert "[unverified]" not in result.answer_text
    assert "Citations:" in result.answer_text
    assert result.citations
    assert "61000" in result.answer_text  # the observed value appears verbatim


async def test_offline_answer_never_invents_a_number(registry, ctx):
    """Every numeric token in the offline NARRATIVE is in the per-turn tool whitelist.

    The grounding guard verifies the narrative draft (the citations footer — appended
    after verification — carries the provenance string ``stub-hash-1024``, which is not a
    data figure), so we check the body above the footer, exactly as the verifier does.
    """

    result = await answer("Why did revenue drop last week?", ctx, registry=registry, llm=None)
    whitelist = result.facts_used
    narrative = result.answer_text.split("Citations:")[0]
    for n in extract_numbers(narrative):
        assert any(
            abs(n - a) <= 0.02 * max(abs(n), 1.0) or abs(abs(n) - abs(a)) <= 0.02 * max(abs(n), 1.0)
            for a in whitelist
        ), f"ungrounded number leaked into offline answer: {n}"


async def test_offline_answer_surfaces_the_recommendation(registry, ctx):
    """A "what should we do" question templates the retrieved recommendation + impact."""

    result = await answer(
        "What should we do about the EMEA revenue drop?", ctx, registry=registry, llm=None
    )
    assert result.route["intent"] == "recommendation"
    # The recovery estimate (170000) from the retrieved recommendation is cited as a fact.
    assert any(abs(n - 170000.0) < 1.0 for n in result.facts_used)
    assert result.grounding_passed is True


async def test_offline_answer_is_tenant_scoped(registry, other_ctx):
    """globex's offline answer is grounded but never carries acme-exclusive facts.

    globex has its OWN copy of the finding (``f-other``, the same -35.8/61000 shape), so
    those figures legitimately appear — they are globex's own data, not a leak. The
    isolation proof is that acme's EXCLUSIVE recommendation (``r-91c`` / 170000 recovery
    and its 0.84 confidence — never seeded for globex) never surfaces for globex.
    """

    result = await answer("Why did revenue drop last week?", other_ctx, registry=registry, llm=None)
    assert result.grounding_passed is True
    # acme's recommendation impact (170000) and its confidence (0.84) are acme-only.
    assert all(abs(n - 170000.0) > 1.0 for n in result.facts_used)
    assert all(abs(n - 0.84) > 1e-6 for n in result.facts_used)


async def test_offline_answer_never_raises_on_empty_data(registry):
    """A tenant with no seeded data yields a safe grounded answer, not an exception."""

    from edis_copilot.tools.base import ToolContext

    empty_ctx = ToolContext.for_tenant("nobody")
    result = await answer("Why did revenue drop last week?", empty_ctx, registry=registry, llm=None)
    assert result.grounding_passed is True  # no numbers -> trivially grounded
    assert "[unverified]" not in result.answer_text
    assert allowed_numbers([]) == []
