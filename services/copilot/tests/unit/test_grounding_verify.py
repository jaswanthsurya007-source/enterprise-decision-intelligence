"""P3 — the grounding verifier: faithful answers pass, fabricated numbers are caught.

The copilot grounding guarantee: every numeric claim in an answer must trace to a value a
tool returned THIS turn. These tests exercise the pure verifier + citation builder against
the demo figures (-8.3% WoW KPI, -35.8% / 61000 / 95000 finding, 1400ms latency, 170000
recovery) so the guard is proven offline, identical for Opus or the offline agent:

* a faithful answer whose every number is in the whitelist passes (``ok``);
* an answer with an unwhitelisted number is flagged (``not ok``) and stripped to
  ``[unverified]`` while grounded figures survive;
* citations resolve — each marker maps to the tool result that contributed its numbers,
  and ``facts_used`` is the de-duplicated union the UI renders as authoritative.
"""

from __future__ import annotations

from edis_copilot.grounding import (
    allowed_numbers,
    build_citations,
    strip_ungrounded_numbers,
    verify_answer,
)
from edis_copilot.tools.base import ToolResult

# The whitelist the demo's tools would return this turn (KPI + finding + recommendation).
_DEMO_RESULTS = [
    ToolResult(
        tool="metric_lookup",
        rows=[{"metric_key": "revenue"}],
        numbers=[-8.3, 385000.0, 420000.0],
        citation="tool metric_lookup: revenue weekly",
    ),
    ToolResult(
        tool="find_anomalies",
        rows=[{"finding_id": "f-7a3"}],
        numbers=[61000.0, 95000.0, -35.8, 0.94, 1400.0],
        citation="tool find_anomalies",
    ),
    ToolResult(
        tool="semantic_search",
        rows=[{"id": "r-91c"}],
        numbers=[170000.0, 0.84],
        citation="tool semantic_search: what should we do",
    ),
]


def test_faithful_answer_passes():
    """Every figure traces to a tool result -> grounded, no unmatched numbers."""

    whitelist = allowed_numbers(_DEMO_RESULTS)
    answer = (
        "Revenue fell 8.3% week over week, from 420000 to 385000. [1] EMEA web revenue "
        "dropped to 61000 from an expected 95000 (a 35.8% drop), with checkout-api latency "
        "of 1400ms the leading driver (correlation 0.94). [2] Recommended action recovers "
        "an estimated 170000. [3]"
    )
    verdict = verify_answer(answer, whitelist)
    assert verdict.ok
    assert verdict.unmatched == ()


def test_unwhitelisted_number_is_flagged_and_stripped():
    """A fabricated figure is flagged, then replaced with [unverified] on strip."""

    whitelist = allowed_numbers(_DEMO_RESULTS)
    answer = "Revenue fell to 61000; it will rebound to 999999 next quarter. [1]"
    verdict = verify_answer(answer, whitelist)
    assert not verdict.ok
    assert any(abs(n - 999999.0) < 1.0 for n in verdict.unmatched)

    stripped = strip_ungrounded_numbers(answer, whitelist)
    assert "61000" in stripped  # grounded figure survives
    assert "999999" not in stripped and "[unverified]" in stripped
    # Re-verifying the stripped text is clean (the marker carries no digits).
    assert verify_answer(stripped, whitelist).ok


def test_percent_sign_does_not_block_a_faithful_drop():
    """A -35.8% stored figure grounds a "35.8% drop" phrasing (sign-insensitive match)."""

    whitelist = allowed_numbers(_DEMO_RESULTS)
    assert verify_answer("a 35.8% drop", whitelist).ok
    assert verify_answer("-8.3% week over week", whitelist).ok


def test_citations_resolve_to_their_tool_results():
    """Each citation marker maps to its tool + numbers; facts_used is the deduped union."""

    cs = build_citations(_DEMO_RESULTS)
    markers = [c.marker for c in cs.citations]
    assert markers == ["[1]", "[2]", "[3]"]
    by_marker = {c.marker: c for c in cs.citations}
    assert by_marker["[2]"].tool == "find_anomalies"
    assert 61000.0 in by_marker["[2]"].numbers and -35.8 in by_marker["[2]"].numbers
    assert by_marker["[3]"].tool == "semantic_search"
    assert 170000.0 in by_marker["[3]"].numbers
    # facts_used is the ordered union across all results (no duplicates).
    assert 61000.0 in cs.facts_used and 170000.0 in cs.facts_used and -8.3 in cs.facts_used
    footer = cs.markers_footer()
    assert footer.startswith("Citations:") and "[1]" in footer and "[3]" in footer
