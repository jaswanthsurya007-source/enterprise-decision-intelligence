"""Grounding verifier + citation builder — golden cases (pure, no infra/keys)."""

from __future__ import annotations

from edis_copilot.grounding import (
    build_citations,
    extract_numbers,
    matches_allowed,
    strip_ungrounded_numbers,
    verify_answer,
)
from edis_copilot.tools.base import ToolResult


def test_extract_numbers_formats():
    nums = extract_numbers("revenue fell 35.8% from $95,000 to $61,000; latency 1.4k ms")
    assert -35.8 not in nums  # plain % is positive 35.8 unless signed
    assert 35.8 in nums
    assert 95000.0 in nums and 61000.0 in nums
    assert 1400.0 in nums  # 1.4k rescales


def test_extract_ignores_identifier_digits():
    # "p95" and "checkout-api" must not yield 95 / api numbers.
    nums = extract_numbers("latency_p95 on checkout-api rose")
    assert 95.0 not in nums


def test_negative_and_range_hyphen():
    assert -35.8 in extract_numbers("a change of -35.8%")
    # A hyphen between two numbers is a range separator, not a sign.
    rng = extract_numbers("between 101,917-106,378")
    assert 106378.0 in rng and -106378.0 not in rng


def test_verify_answer_ok_when_all_grounded():
    res = verify_answer("revenue fell to 61000 from 95000", [61000.0, 95000.0])
    assert res.ok and res.unmatched == ()


def test_verify_answer_flags_ungrounded():
    res = verify_answer("revenue fell to 61000; projected 200000 next year", [61000.0])
    assert not res.ok and 200000.0 in res.unmatched


def test_verify_no_numbers_is_trivially_grounded():
    assert verify_answer("a sharp drop in EMEA web revenue", []).ok


def test_matches_allowed_tolerance():
    assert matches_allowed(95000.0, [95001.0], 0.02)  # within 2%
    assert not matches_allowed(95000.0, [80000.0], 0.02)


def test_strip_ungrounded_replaces_only_bad_numbers():
    out = strip_ungrounded_numbers("revenue 61000 with a forecast of 999999", [61000.0])
    assert "61000" in out and "999999" not in out and "[unverified]" in out
    # Re-verifying the stripped text is clean (marker carries no digits).
    assert verify_answer(out, [61000.0]).ok


def test_build_citations_numbers_and_facts():
    results = [
        ToolResult(
            tool="metric_lookup",
            rows=[{}],
            numbers=[95000.0, 61000.0],
            citation="tool metric_lookup: revenue",
        ),
        ToolResult(
            tool="find_anomalies",
            rows=[{}],
            numbers=[-35.8, 95000.0],
            citation="tool find_anomalies",
        ),
    ]
    cs = build_citations(results)
    assert [c.marker for c in cs.citations] == ["[1]", "[2]"]
    # facts_used is the de-duplicated union (95000 appears once).
    assert 95000.0 in cs.facts_used and -35.8 in cs.facts_used
    assert cs.facts_used.count(95000.0) == 1
    footer = cs.markers_footer()
    assert footer.startswith("Citations:") and "[1]" in footer and "[2]" in footer
