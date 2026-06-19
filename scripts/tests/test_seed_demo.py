"""Unit tests for the pure (infra-free) parts of scripts/seed_demo.py (Z1).

These exercise the side-effect-free scenario-construction + story/copilot-answer
formatting functions only — no httpx, no live stack, no clock dependence — so they
run under ``make test`` with no Docker and no API key.
"""

from __future__ import annotations

from datetime import UTC, datetime

from scripts.seed_demo import (
    DEFAULT_SCENARIO,
    build_scenario,
    format_copilot_answer,
    format_story,
    pick_revenue_finding,
    pick_top_recommendation,
    scenario_anchor_day,
    scenario_inject_body,
    seed_request_body,
    summarize_finding,
)

# A §9-shaped finding + recommendation (the numbers the formatter must echo, never invent).
_FINDING = {
    "finding_id": "f-7a3",
    "tenant_id": "acme",
    "kind": "level_shift",
    "metric_key": "revenue",
    "dimensions": {"region": "EMEA", "channel": "web"},
    "observed_value": 61000.0,
    "expected_value": 95000.0,
    "deviation": -34000.0,
    "deviation_pct": -35.8,
    "score": 5.8,
    "confidence": 0.91,
    "window_start": "2026-06-12T00:00:00Z",
    "window_end": "2026-06-18T23:59:59Z",
    "candidate_causes": [
        {
            "metric_key": "latency_p95",
            "correlation": 0.94,
            "lag_minutes": 120,
            "contribution_pct": 71.0,
            "observed_delta": 1220.0,
        }
    ],
}

_REC = {
    "recommendation_id": "r-91c",
    "title": "Mitigate checkout-api latency in EMEA (likely deploy regression)",
    "action_type": "operational_fix",
    "priority_rank": 1,
    "priority_score": 0.93,
    "impact": {
        "value": 170000.0,
        "value_low": 120000.0,
        "value_high": 200000.0,
        "unit": "USD",
        "horizon_days": 5,
    },
    "confidence": {
        "value": 0.84,
        "components": {"insight": 0.91, "evidence": 0.88, "historical_calibration": 0.74},
    },
}


def test_scenario_anchor_day_is_seven_days_before_now() -> None:
    now = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)
    assert scenario_anchor_day(now).isoformat() == "2026-06-12"


def test_build_scenario_resolves_revenue_drop_emea() -> None:
    now = datetime(2026, 6, 19, tzinfo=UTC)
    scenario, anchor, duration = build_scenario(DEFAULT_SCENARIO, now=now)
    assert scenario.name == "revenue_drop_emea"
    assert anchor.isoformat() == "2026-06-12"
    assert duration == 5


def test_seed_request_body_shape() -> None:
    body = seed_request_body(days=90, seed=42)
    assert body == {"days": 90, "seed": 42, "scenario": None}


def test_scenario_inject_body_carries_timing_params() -> None:
    body = scenario_inject_body(anchor_day=scenario_anchor_day(datetime(2026, 6, 19, tzinfo=UTC)))
    assert body["scenario"] == "revenue_drop_emea"
    assert body["params"]["anchor_day"] == "2026-06-12"
    assert body["params"]["duration_days"] == 5
    # exactly-one-of profile|scenario: we never send a profile alongside a scenario
    assert "profile" not in body


def test_pickers_select_the_demo_headline_facts() -> None:
    findings = [
        {"metric_key": "orders", "kind": "point_anomaly"},
        _FINDING,
        {"metric_key": "revenue", "kind": "point_anomaly"},
    ]
    assert pick_revenue_finding(findings) is _FINDING  # prefers the revenue level_shift
    assert pick_revenue_finding([]) is None
    recs = [{"priority_rank": 2}, _REC]
    assert pick_top_recommendation(recs) is _REC
    assert pick_top_recommendation([]) is None


def test_summarize_finding_copies_computed_numbers() -> None:
    s = summarize_finding(_FINDING)
    assert s["present"] is True
    assert s["observed_value"] == 61000.0
    assert s["deviation_pct"] == -35.8
    assert s["score"] == 5.8
    assert s["candidate_causes"][0]["contribution_pct"] == 71.0
    assert summarize_finding(None) == {"present": False}


def test_format_copilot_answer_grounds_every_number() -> None:
    answer = format_copilot_answer(
        _FINDING, _REC, wow_total_before=420000.0, wow_total_after=385000.0
    )
    # WoW headline (8.3%) and the EMEA-web facts, all from the inputs (never invented).
    assert "8.3%" in answer
    assert "EMEA web revenue" in answer
    assert "35.8%" in answer
    assert "5.8" in answer  # sigma
    assert "$170K" in answer  # recovery impact
    assert "0.84" in answer  # confidence
    assert "checkout-api" in answer
    # citations present
    assert "[1]" in answer and "[4]" in answer


def test_format_copilot_answer_degrades_without_facts() -> None:
    answer = format_copilot_answer(None, None)
    assert "No revenue anomaly" in answer
    assert "No recommendation" in answer
    # no fabricated dollar figures when facts are absent
    assert "$" not in answer


def test_format_story_includes_all_layers_and_the_answer() -> None:
    story = format_story(_FINDING, _REC, tenant_id="acme", scenario="revenue_drop_emea")
    for tag in (
        "L1 Ingestion",
        "L2 Integration",
        "L3 Intelligence",
        "L4 Decision",
        "L6 Dashboard",
        "L5 Copilot",
    ):
        assert tag in story
    assert "f-7a3" in story  # the finding id
    assert "r-91c" in story  # the recommendation id
    assert "revenue_drop_emea" in story


def test_format_story_handles_missing_facts() -> None:
    story = format_story(None, None)
    assert "no finding surfaced yet" in story
    assert "no recommendation produced yet" in story
