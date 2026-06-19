"""X4 scoring tests -- severity / confidence / business_impact_input normalization.

Locks the X1 scoring contract (``scoring/normalize.py``): all three normalized
fields are pure, deterministic, **bounded to [0, 1]**, and **monotonic** in the
quantity they measure:

* ``severity`` increases with ``|score|`` (a logistic in the detector-native
  z / residual-sigma), never escaping [0, 1].
* ``confidence`` increases with both the margin over the detection threshold and
  the amount of baseline history; bounded [0, 1].
* ``business_impact_input`` = magnitude x direction x reach, bounded [0, 1];
  an *adverse* move scores higher than an equal favorable move; a more tightly
  scoped (more-dimensions) cell scores no higher than an unscoped one.

``score_result`` folds these onto a copy of the DetectorResult without mutating
the detector core. All infra-free.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from edis_contracts.findings import FindingKind

from edis_intelligence.detectors.base import DetectionContext, DetectorResult
from edis_intelligence.scoring.normalize import (
    business_impact_input,
    confidence,
    score_result,
    severity,
)

_NOW = datetime(2026, 6, 19, tzinfo=timezone.utc)


def _result(
    *,
    score: float,
    deviation_pct: float = -36.0,
    deviation: float = -34000.0,
    dimensions: dict[str, str] | None = None,
    detector: str = "stl_seasonal",
    baseline_n: float = 28.0,
) -> DetectorResult:
    return DetectorResult(
        detector=detector,
        detector_version="1.0",
        kind=FindingKind.LEVEL_SHIFT,
        metric_key="revenue",
        dimensions=dimensions if dimensions is not None else {"region": "EMEA"},
        window_start=_NOW,
        window_end=_NOW,
        observed_value=61000.0,
        expected_value=95000.0,
        deviation=deviation,
        deviation_pct=deviation_pct,
        score=score,
        diagnostics={"baseline_n": baseline_n},
    )


def _ctx(direction: str = "down", **over) -> DetectionContext:
    return DetectionContext(
        tenant_id="acme",
        metric_key="revenue",
        dimensions={"region": "EMEA"},
        direction=direction,
        **over,
    )


# ---------------------------------------------------------------------------
# bounds
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("score", [0.0, 0.5, 2.0, 3.5, 5.8, 12.0, 100.0, -50.0])
def test_severity_bounded_0_1(score: float) -> None:
    s = severity(_result(score=score))
    assert 0.0 <= s <= 1.0


@pytest.mark.parametrize("score", [0.0, 3.5, 7.0, 40.0])
def test_confidence_bounded_0_1(score: float) -> None:
    c = confidence(_result(score=score), _ctx())
    assert 0.0 <= c <= 1.0


@pytest.mark.parametrize("pct", [0.0, 10.0, 50.0, 100.0, 250.0, -300.0])
def test_business_impact_bounded_0_1(pct: float) -> None:
    b = business_impact_input(_result(score=5.0, deviation_pct=pct), _ctx())
    assert 0.0 <= b <= 1.0


# ---------------------------------------------------------------------------
# monotonicity
# ---------------------------------------------------------------------------
def test_severity_monotonic_in_score() -> None:
    scores = [0.0, 1.0, 2.0, 3.5, 4.0, 5.8, 8.0, 20.0]
    sev = [severity(_result(score=s)) for s in scores]
    assert sev == sorted(sev)  # non-decreasing
    assert sev[-1] > sev[0]  # and genuinely increasing across the range
    # magnitude only: a negative score scores the same as its positive twin
    assert severity(_result(score=-5.8)) == pytest.approx(severity(_result(score=5.8)))


def test_confidence_monotonic_in_margin() -> None:
    ctx = _ctx()
    margins = [3.5, 4.0, 5.0, 7.0, 10.0, 20.0]
    confs = [confidence(_result(score=m), ctx) for m in margins]
    assert confs == sorted(confs)
    assert confs[-1] > confs[0]


def test_confidence_monotonic_in_history() -> None:
    ctx = _ctx()
    confs = [confidence(_result(score=6.0, baseline_n=n), ctx) for n in (1.0, 7.0, 14.0, 28.0)]
    assert confs == sorted(confs)
    assert confs[-1] > confs[0]


def test_business_impact_monotonic_in_magnitude() -> None:
    ctx = _ctx()
    bs = [business_impact_input(_result(score=5.0, deviation_pct=p), ctx) for p in (5, 20, 50, 90)]
    assert bs == sorted(bs)
    assert bs[-1] > bs[0]


# ---------------------------------------------------------------------------
# direction + reach semantics
# ---------------------------------------------------------------------------
def test_adverse_direction_scores_higher_than_favorable() -> None:
    adverse = business_impact_input(
        _result(score=5.0, deviation_pct=-36.0, deviation=-34000.0), _ctx("down")
    )
    favorable = business_impact_input(
        _result(score=5.0, deviation_pct=36.0, deviation=34000.0), _ctx("down")
    )
    assert adverse > favorable


def test_both_direction_treats_either_move_fully() -> None:
    up = business_impact_input(
        _result(score=5.0, deviation_pct=36.0, deviation=34000.0), _ctx("both")
    )
    down = business_impact_input(
        _result(score=5.0, deviation_pct=-36.0, deviation=-34000.0), _ctx("both")
    )
    assert up == pytest.approx(down)


def test_reach_unscoped_geq_scoped() -> None:
    ctx = _ctx("both")
    unscoped = business_impact_input(
        _result(score=5.0, deviation_pct=80.0, deviation=1.0, dimensions={}), ctx
    )
    scoped = business_impact_input(
        _result(
            score=5.0,
            deviation_pct=80.0,
            deviation=1.0,
            dimensions={"region": "EMEA", "channel": "web", "service": "checkout-api"},
        ),
        ctx,
    )
    assert unscoped >= scoped


# ---------------------------------------------------------------------------
# score_result -- pure fold
# ---------------------------------------------------------------------------
def test_score_result_fills_and_does_not_mutate() -> None:
    raw = _result(score=5.8)
    ctx = _ctx()
    scored = score_result(raw, ctx)
    # original untouched (detector core stays scoring-free)
    assert raw.severity == 0.0 and raw.confidence == 0.0 and raw.business_impact_input == 0.0
    # copy carries the bounded normalized scores
    assert scored.severity == pytest.approx(severity(raw))
    assert scored.confidence == pytest.approx(confidence(raw, ctx))
    assert scored.business_impact_input == pytest.approx(business_impact_input(raw, ctx))
    for v in (scored.severity, scored.confidence, scored.business_impact_input):
        assert 0.0 <= v <= 1.0
    # identity fields preserved
    assert scored.score == raw.score and scored.metric_key == raw.metric_key


def test_score_result_deterministic() -> None:
    raw = _result(score=5.8)
    ctx = _ctx()
    a, b = score_result(raw, ctx), score_result(raw, ctx)
    assert (a.severity, a.confidence, a.business_impact_input) == (
        b.severity,
        b.confidence,
        b.business_impact_input,
    )
