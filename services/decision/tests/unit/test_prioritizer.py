"""Unit tests for the deterministic :class:`Prioritizer`.

Asserts: the raw priority formula ``(impact.value * confidence.value) / effort_units`` is
exact; the effort-tier map is monotone with the documented floor; the normalized
``priority_score`` is in [0, 1], monotone in the raw score, and ~0.5 at the anchor; and
``rank`` orders candidates highest-raw-score-first with deterministic id tie-breaking.
Pure -- no infra.
"""

from __future__ import annotations

from edis_contracts.decisions import ConfidenceScore, ImpactEstimate

from decision_engine.scoring.prioritizer import (
    DEFAULT_EFFORT_FLOOR,
    DEFAULT_NORM_ANCHOR,
    EFFORT_UNITS,
    PriorityInputs,
    Prioritizer,
    effort_units,
)


def _impact(value: float) -> ImpactEstimate:
    return ImpactEstimate(
        value=value,
        value_low=value * 0.7,
        value_high=value * 1.3,
        unit="USD",
        direction="increase",
        horizon_days=5,
        inputs={},
        method="recovery_flat",
    )


def _conf(value: float) -> ConfidenceScore:
    return ConfidenceScore(value=value, components={}, calibration_n=0)


def test_effort_units_map_is_monotone_with_floor():
    """Smaller tier => fewer units; each is the table value plus the floor."""

    for tier in ("xs", "s", "m", "l", "xl"):
        assert effort_units(tier) == EFFORT_UNITS[tier] + DEFAULT_EFFORT_FLOOR
    assert (
        effort_units("xs")
        < effort_units("s")
        < effort_units("m")
        < effort_units("l")
        < effort_units("xl")
    )


def test_raw_score_formula_is_exact():
    """raw = (impact.value * confidence.value) / effort_units(tier)."""

    p = Prioritizer()
    raw = p.raw_score(_impact(170000.0), _conf(0.84), "s")

    expected = (170000.0 * 0.84) / effort_units("s")
    assert raw == expected


def test_priority_score_is_bounded_and_monotone():
    """Normalized score is in [0, 1] and strictly increases with the raw score."""

    p = Prioritizer()
    small = p.priority_score(_impact(10000.0), _conf(0.5), "m")
    big = p.priority_score(_impact(500000.0), _conf(0.95), "xs")

    assert 0.0 <= small <= 1.0
    assert 0.0 <= big <= 1.0
    assert big > small


def test_priority_score_is_half_at_the_anchor():
    """A single anchor-sized, small-effort, full-confidence rec reads ~0.5."""

    p = Prioritizer(norm_anchor=DEFAULT_NORM_ANCHOR)
    # raw == anchor / effort_units("s") when value*conf == anchor and tier == s.
    score = p.priority_score(_impact(DEFAULT_NORM_ANCHOR), _conf(1.0), "s")

    assert score == 0.5


def test_demo_priority_score_reads_high():
    """The §9 demo (~$170K, 0.84, effort s) reads ~0.93 -- well above 0.5."""

    p = Prioritizer()
    score = p.priority_score(_impact(170000.0), _conf(0.8342), "s")

    assert 0.9 <= score <= 0.95


def test_single_candidate_is_rank_1():
    p = Prioritizer()
    ranks = p.rank([PriorityInputs("r1", _impact(170000.0), _conf(0.84), "s")])
    assert ranks == {"r1": 1}


def test_ranking_orders_by_raw_score_descending():
    """Higher (impact*confidence/effort) -> better (lower) rank number."""

    p = Prioritizer()
    candidates = [
        # raw = 100000*0.9/effort(s)=2.5 -> 36000
        PriorityInputs("low-effort-high-value", _impact(100000.0), _conf(0.9), "s"),
        # raw = 100000*0.9/effort(xl)=16.5 -> ~5454
        PriorityInputs("high-effort", _impact(100000.0), _conf(0.9), "xl"),
        # raw = 50000*0.5/effort(m)=4.5 -> ~5555
        PriorityInputs("mid", _impact(50000.0), _conf(0.5), "m"),
    ]

    ranks = p.rank(candidates)

    # low-effort-high-value has the largest raw score -> rank 1.
    assert ranks["low-effort-high-value"] == 1
    ordered = sorted(ranks, key=lambda k: ranks[k])
    raws = [
        p.raw_score(c.impact, c.confidence, c.effort_tier)
        for name in ordered
        for c in candidates
        if c.recommendation_id == name
    ]
    assert raws == sorted(raws, reverse=True)


def test_ranking_ties_broken_by_id_ascending():
    """Equal raw scores -> deterministic order by recommendation id ascending."""

    p = Prioritizer()
    a = PriorityInputs("aaa", _impact(100000.0), _conf(0.8), "s")
    b = PriorityInputs("bbb", _impact(100000.0), _conf(0.8), "s")

    # Pass in reverse to prove the order is by id, not input order.
    ranks = p.rank([b, a])

    assert ranks == {"aaa": 1, "bbb": 2}


def test_normalization_preserves_ranking():
    """The normalized score is monotone in raw, so ranking by either is identical."""

    p = Prioritizer()
    candidates = [
        PriorityInputs("x", _impact(170000.0), _conf(0.84), "s"),
        PriorityInputs("y", _impact(40000.0), _conf(0.6), "m"),
        PriorityInputs("z", _impact(9000.0), _conf(0.5), "l"),
    ]

    by_rank = p.rank(candidates)
    by_norm = sorted(
        candidates,
        key=lambda c: -p.priority_score(c.impact, c.confidence, c.effort_tier),
    )
    norm_order = {c.recommendation_id: i + 1 for i, c in enumerate(by_norm)}

    assert by_rank == norm_order
