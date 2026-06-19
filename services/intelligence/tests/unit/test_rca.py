"""X4 RCA tests -- lag-correlation ranking, dimensional decomposition, evidence.

Locks the X2 RCA contract (architecture §5.3 + §9):

* lag-aware cross-correlation ranks a **leading** driver above a **coincident** one
  and drops an **unrelated** series (below the correlation floor);
* the leading driver's ``direction`` is recovered from the sign of its best lag,
  and its ``lag_minutes`` is positive (it moved before the target);
* dimensional decomposition attributes an aggregate change to the right dimension
  cell (EMEA), summing to ~100%;
* the evidence bundler places every cited candidate-cause figure (correlation, lag,
  contribution, observed_delta) AND the target headline numbers into
  ``allowed_numbers`` -- the grounding whitelist.

All pure / deterministic / infra-free.
"""

from __future__ import annotations

from uuid import uuid4

import numpy as np
import pytest

from edis_contracts.findings import EvidenceBundle

from edis_intelligence.detectors.base import DetectionContext
from edis_intelligence.detectors.stl_seasonal import StlSeasonalDetector
from edis_intelligence.rca.correlation import (
    Candidate,
    cross_correlate,
    rank_candidate_causes,
)
from edis_intelligence.rca.decomposition import (
    contribution_pct_from_causes,
    dimensional_contributions,
)
from edis_intelligence.rca.evidence import build_evidence_bundle
from edis_intelligence.scoring.normalize import score_result

from edis_l3_testkit import build_demo_series  # type: ignore[import-not-found]


def _target_scored():
    rev, _, _ = build_demo_series()
    ctx = DetectionContext(
        tenant_id="acme",
        metric_key="revenue",
        dimensions={"region": "EMEA", "channel": "web"},
        direction="down",
    )
    results = StlSeasonalDetector().detect(rev, ctx)
    assert results, "STL must flag the level shift"
    return score_result(results[0], ctx)


# ---------------------------------------------------------------------------
# leading vs coincident vs unrelated
# ---------------------------------------------------------------------------
def test_lag_correlation_ranks_leading_above_coincident_and_unrelated() -> None:
    rng = np.random.default_rng(123)
    rev, lat, err = build_demo_series()

    # A coincident driver: moves with revenue at lag 0 (no lead, no lag). Made
    # deliberately noisier than the ops spike so its |correlation| is below the
    # leading driver's -- we are testing direction + the floor, not who out-correlates
    # whom (a perfect coincident mirror would legitimately out-rank a noisier lead).
    coincident = [(t, 200.0 - 0.001 * v + rng.normal(0, 8)) for t, v in rev]

    # An unrelated driver: flat noise (|correlation| well below the floor).
    unrelated = [(t, 50.0 + rng.normal(0, 2)) for t, _ in rev]

    cands = [
        Candidate("latency_p95", {"region": "EMEA", "service": "checkout-api"}, lat),
        Candidate("coincident_metric", {"region": "EMEA"}, coincident),
        Candidate("unrelated_metric", {"region": "NA"}, unrelated),
    ]
    # coincident_band=0: the demo's 1-day-lead ops spike reads as a genuine lead
    # (a negative best lag), distinguishing it from the lag-0 coincident driver.
    causes = rank_candidate_causes(
        rev, cands, max_lag=5, coincident_band=0, min_abs_correlation=0.5
    )
    by_key = {c.metric_key: c for c in causes}
    # the unrelated, low-correlation series is dropped below the floor
    assert "unrelated_metric" not in by_key
    # the leading ops driver is recovered AS leading, with a positive lag
    assert "latency_p95" in by_key
    assert by_key["latency_p95"].direction == "leading"
    assert by_key["latency_p95"].lag_minutes > 0  # moved before the target
    # the lag-0 coincident driver is labeled coincident -- never "leading"
    assert "coincident_metric" in by_key
    assert by_key["coincident_metric"].direction == "coincident"
    # ranking is by |correlation| desc: the leading driver out-correlates the noisier
    # coincident one here, so it ranks ahead of it
    assert causes.index(by_key["latency_p95"]) < causes.index(by_key["coincident_metric"])


def test_cross_correlate_recovers_leading_negative_sign() -> None:
    rev, lat, _ = build_demo_series()
    # coincident_band=0 so the 1-sample lead is reported as a lead (negative lag).
    lc = cross_correlate(rev, lat, max_lag=5, coincident_band=0)
    # latency up while revenue down -> strong NEGATIVE level correlation
    assert lc.correlation < -0.5, lc.correlation
    assert lc.direction == "leading"
    assert lc.lag_minutes > 0  # the spike preceded the drop
    assert lc.observed_delta > 0  # latency went up


def test_cross_correlate_coincident_band_absorbs_one_sample_lead() -> None:
    # With coincident_band=1, a 1-sample lead is treated as coincident -- the
    # pipeline's default at daily resolution (a one-day lead is "same window").
    rev, lat, _ = build_demo_series()
    lc = cross_correlate(rev, lat, max_lag=5, coincident_band=1)
    assert lc.correlation < -0.5
    assert lc.direction == "coincident"


def test_rca_drops_all_below_correlation_floor() -> None:
    rev, _, _ = build_demo_series()
    rng = np.random.default_rng(77)
    noise = [(t, 10.0 + rng.normal(0, 1)) for t, _ in rev]
    causes = rank_candidate_causes(
        rev, [Candidate("noise", {}, noise)], max_lag=5, min_abs_correlation=0.6
    )
    assert causes == []


# ---------------------------------------------------------------------------
# dimensional decomposition
# ---------------------------------------------------------------------------
def test_decomposition_attributes_change_to_emea() -> None:
    cells = {
        "EMEA": (95_000.0, 61_000.0),  # the big drop
        "NA": (110_000.0, 109_000.0),
        "APAC": (80_000.0, 79_500.0),
        "LATAM": (60_000.0, 60_500.0),
    }
    contribs = dimensional_contributions(cells)
    assert contribs[0].dimension_value == "EMEA"
    assert contribs[0].contribution_pct > 90.0
    assert contribs[0].delta < 0
    assert sum(c.contribution_pct for c in contribs) == pytest.approx(100.0, abs=0.5)


def test_contribution_pct_from_causes_sums_to_100_latency_leads() -> None:
    rev, lat, err = build_demo_series()
    cands = [
        Candidate("latency_p95", {"region": "EMEA", "service": "checkout-api"}, lat),
        Candidate("error_rate", {"region": "EMEA", "service": "checkout-api"}, err),
    ]
    weighted = contribution_pct_from_causes(
        rank_candidate_causes(rev, cands, max_lag=5, coincident_band=1)
    )
    assert weighted[0].metric_key == "latency_p95"
    assert sum(c.contribution_pct for c in weighted) == pytest.approx(100.0, abs=0.2)
    assert weighted[0].contribution_pct >= weighted[1].contribution_pct


# ---------------------------------------------------------------------------
# evidence allowed_numbers contains the cited figures
# ---------------------------------------------------------------------------
def _rel_present(value: float, allowed: list[float], tol: float = 0.02) -> bool:
    return any(abs(value - a) <= tol * max(abs(value), 1.0) for a in allowed)


def test_evidence_allowed_numbers_contains_cited_figures() -> None:
    target = _target_scored()
    rev, lat, err = build_demo_series()
    causes = contribution_pct_from_causes(
        rank_candidate_causes(
            rev,
            [
                Candidate("latency_p95", {"region": "EMEA", "service": "checkout-api"}, lat),
                Candidate("error_rate", {"region": "EMEA", "service": "checkout-api"}, err),
            ],
            max_lag=5,
            coincident_band=1,
        )
    )
    dims = dimensional_contributions({"EMEA": (95_000.0, 61_000.0), "NA": (110_000.0, 109_000.0)})

    bundle = build_evidence_bundle(
        tenant_id="acme",
        finding_id=uuid4(),
        target=target,
        candidate_causes=causes,
        dimension_contributions=[("revenue", d) for d in dims],
    )

    assert isinstance(bundle, EvidenceBundle)
    allowed = bundle.allowed_numbers
    # target headline figures
    for v in (
        target.observed_value,
        target.expected_value,
        target.deviation,
        round(target.deviation_pct, 1),
    ):
        assert _rel_present(v, allowed), v
    # every candidate-cause figure the narrator may cite
    for c in causes:
        assert _rel_present(c.correlation, allowed), c.correlation
        assert _rel_present(float(c.lag_minutes), allowed), c.lag_minutes
        assert _rel_present(c.observed_delta, allowed), c.observed_delta
        assert _rel_present(c.contribution_pct, allowed), c.contribution_pct
    # the EMEA dimensional contribution figure
    emea = dims[0]
    assert _rel_present(emea.contribution_pct, allowed), emea.contribution_pct
    assert _rel_present(emea.delta, allowed), emea.delta
