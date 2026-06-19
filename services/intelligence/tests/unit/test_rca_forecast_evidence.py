"""X2 unit tests -- lag-aware RCA, dimensional contribution, ETS forecast, evidence.

These lock the X2 analysis contract against the architecture §9 demo
(``revenue_drop_emea``): given an EMEA-web ``revenue`` level shift and contemporaneous
EMEA ``checkout-api`` ``latency_p95`` / ``error_rate`` spikes, RCA must

* rank the latency + error spikes as **leading** causes of the revenue level shift,
  with the latency spike ranked first (strongest |correlation|), and
* attribute the aggregate revenue change overwhelmingly to the **EMEA** dimension;

the ETS forecaster must produce a ``statsmodels.ETS`` point + band; and the evidence
bundler must place every cited figure (observed / expected / deviation / pct, each
candidate cause's correlation / contribution / delta / lag, the baseline, and the
forecast band) into ``EvidenceBundle.allowed_numbers`` -- the grounding whitelist.

All pure / deterministic / infra-free (no Docker, no API keys).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import numpy as np
import pytest

from edis_contracts.findings import EvidenceBundle, Forecast

from edis_intelligence.detectors.base import DetectionContext
from edis_intelligence.detectors.stl_seasonal import StlSeasonalDetector
from edis_intelligence.forecast.ets_model import MODEL_NAME, forecast_series
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

_START = datetime(2026, 5, 15, tzinfo=timezone.utc)
_WEEKLY = [1.05, 1.0, 0.98, 1.02, 1.1, 0.92, 0.93]  # day-of-week seasonality
_BASELINE_DAYS = 28
_INCIDENT_DAYS = 7


def _demo_series() -> tuple[list, list, list]:
    """Build the §9-shaped daily series (deterministic, seed=42).

    revenue ~$95K/day weekly-seasonal dropping ~36% during the incident; latency p95
    ~180ms -> ~1400ms and error_rate ~0.4% -> ~9% spiking one day *before* the revenue
    drop (so RCA sees them lead).
    """

    rng = np.random.default_rng(42)
    n = _BASELINE_DAYS + _INCIDENT_DAYS
    days = [_START + timedelta(days=i) for i in range(n)]
    rev, lat, err = [], [], []
    for i, d in enumerate(days):
        base = 95_000 * _WEEKLY[d.weekday()] + rng.normal(0, 1500)
        if i >= _BASELINE_DAYS:
            base *= 0.64  # ~ -36%
        rev.append((d, base))
        # ops spike begins one day before the revenue drop (leading)
        if i >= _BASELINE_DAYS - 1:
            lat.append((d, 1400 + rng.normal(0, 40)))
            err.append((d, 0.09 + rng.normal(0, 0.005)))
        else:
            lat.append((d, 180 + rng.normal(0, 10)))
            err.append((d, 0.004 + rng.normal(0, 0.001)))
    return rev, lat, err


def _target_result():
    rev, _, _ = _demo_series()
    ctx = DetectionContext(
        tenant_id="acme",
        metric_key="revenue",
        dimensions={"region": "EMEA", "channel": "web"},
        direction="down",
    )
    results = StlSeasonalDetector().detect(rev, ctx)
    assert results, "STL must flag the level shift"
    return score_result(results[0], ctx), ctx


# ---------------------------------------------------------------------------
# correlation / ranking
# ---------------------------------------------------------------------------
def test_cross_correlate_recovers_leading_negative_relationship() -> None:
    rev, lat, _ = _demo_series()
    lc = cross_correlate(rev, lat, max_lag=5)
    # latency rises while revenue falls -> strong NEGATIVE level correlation
    assert lc.correlation < -0.5, lc.correlation
    # the spike precedes the drop -> the candidate leads the target
    assert lc.direction == "leading", lc
    assert lc.observed_delta > 0  # latency went up


def test_rca_ranks_latency_and_error_as_leading_causes() -> None:
    rev, lat, err = _demo_series()
    cands = [
        Candidate("latency_p95", {"region": "EMEA", "service": "checkout-api"}, lat),
        Candidate("error_rate", {"region": "EMEA", "service": "checkout-api"}, err),
    ]
    causes = rank_candidate_causes(rev, cands, max_lag=5)
    assert len(causes) == 2, causes
    # latency is the top-ranked cause (largest |correlation|)
    assert causes[0].metric_key == "latency_p95", causes
    assert {c.metric_key for c in causes} == {"latency_p95", "error_rate"}
    for c in causes:
        assert c.direction == "leading", c
        assert abs(c.correlation) > 0.5, c

    # contribution split sums to ~100 and latency carries the larger share
    weighted = contribution_pct_from_causes(causes)
    assert weighted[0].metric_key == "latency_p95"
    assert weighted[0].contribution_pct is not None
    total = sum(c.contribution_pct for c in weighted)
    assert abs(total - 100.0) < 0.5, total
    assert weighted[0].contribution_pct >= weighted[1].contribution_pct


def test_rca_filters_uncorrelated_candidate() -> None:
    rev, _, _ = _demo_series()
    rng = np.random.default_rng(7)
    noise = [(ts, 100.0 + rng.normal(0, 5)) for ts, _ in rev]  # flat, unrelated
    cands = [Candidate("page_views", {"region": "NA"}, noise)]
    causes = rank_candidate_causes(rev, cands, max_lag=5, min_abs_correlation=0.5)
    assert causes == []


# ---------------------------------------------------------------------------
# dimensional decomposition
# ---------------------------------------------------------------------------
def test_dimensional_contribution_attributes_change_to_emea() -> None:
    cells = {
        "EMEA": (95_000.0, 61_000.0),  # the big drop
        "NA": (110_000.0, 109_000.0),
        "APAC": (80_000.0, 79_500.0),
        "LATAM": (60_000.0, 60_500.0),
    }
    contribs = dimensional_contributions(cells)
    assert contribs[0].dimension_value == "EMEA"
    assert contribs[0].contribution_pct > 90.0, contribs[0]
    assert contribs[0].delta < 0  # EMEA fell
    assert sum(c.contribution_pct for c in contribs) == pytest.approx(100.0, abs=0.5)


def test_dimensional_contribution_no_movement_is_zero() -> None:
    contribs = dimensional_contributions({"EMEA": (100.0, 100.0), "NA": (50.0, 50.0)})
    assert all(c.contribution_pct == 0.0 for c in contribs)


# ---------------------------------------------------------------------------
# ETS forecast
# ---------------------------------------------------------------------------
def test_ets_forecast_produces_band() -> None:
    rev, _, _ = _demo_series()
    fc = forecast_series(
        rev,
        tenant_id="acme",
        metric_key="revenue",
        dimensions={"region": "EMEA", "channel": "web"},
        horizon_days=7,
    )
    assert isinstance(fc, Forecast)
    assert fc.model == MODEL_NAME == "statsmodels.ETS"
    assert fc.horizon_days == 7
    assert len(fc.points) == 7
    for p in fc.points:
        assert set(p) == {"ts", "yhat", "yhat_lower", "yhat_upper"}
        assert p["yhat_lower"] <= p["yhat"] <= p["yhat_upper"]
        assert p["yhat_lower"] >= 0.0  # non-negative metric floored
    # band widens with horizon
    w0 = fc.points[0]["yhat_upper"] - fc.points[0]["yhat_lower"]
    wN = fc.points[-1]["yhat_upper"] - fc.points[-1]["yhat_lower"]
    assert wN >= w0


def test_ets_forecast_short_series_still_bands() -> None:
    # Fewer than 2 periods of weekly data: must degrade, not raise.
    days = [_START + timedelta(days=i) for i in range(6)]
    short = [(d, 100.0 + i) for i, d in enumerate(days)]
    fc = forecast_series(short, tenant_id="acme", metric_key="orders", horizon_days=3)
    assert len(fc.points) == 3
    assert fc.model == "statsmodels.ETS"


# ---------------------------------------------------------------------------
# evidence bundle + grounding whitelist
# ---------------------------------------------------------------------------
def _rel_present(value: float, allowed: list[float], tol: float = 0.02) -> bool:
    return any(abs(value - a) <= tol * max(abs(value), 1.0) for a in allowed)


def test_evidence_bundle_contains_every_cited_number() -> None:
    target, _ = _target_result()
    rev, lat, err = _demo_series()
    cands = [
        Candidate("latency_p95", {"region": "EMEA", "service": "checkout-api"}, lat),
        Candidate("error_rate", {"region": "EMEA", "service": "checkout-api"}, err),
    ]
    causes = contribution_pct_from_causes(
        rank_candidate_causes(rev, cands, max_lag=5, coincident_band=1)
    )
    dims = dimensional_contributions({"EMEA": (95_000.0, 61_000.0), "NA": (110_000.0, 109_000.0)})
    fc = forecast_series(rev, tenant_id="acme", metric_key="revenue", horizon_days=7)

    finding_id = uuid4()
    bundle = build_evidence_bundle(
        tenant_id="acme",
        finding_id=finding_id,
        target=target,
        candidate_causes=causes,
        dimension_contributions=[("revenue", d) for d in dims],
        forecast=fc,
    )

    assert isinstance(bundle, EvidenceBundle)
    assert bundle.finding_id == finding_id
    assert bundle.tenant_id == "acme"
    kinds = {i.kind for i in bundle.items}
    assert {"metric_window", "baseline", "candidate_cause", "forecast"} <= kinds

    allowed = bundle.allowed_numbers
    # every headline finding figure
    for v in (
        target.observed_value,
        target.expected_value,
        target.deviation,
        round(target.deviation_pct, 1),
        target.score,
    ):
        assert _rel_present(v, allowed), v
    # every candidate-cause figure
    for c in causes:
        assert _rel_present(c.correlation, allowed), c.correlation
        assert _rel_present(float(c.lag_minutes), allowed), c.lag_minutes
        assert _rel_present(c.observed_delta, allowed), c.observed_delta
        assert _rel_present(c.contribution_pct, allowed), c.contribution_pct
    # forecast band
    p0 = fc.points[0]
    for v in (p0["yhat"], p0["yhat_lower"], p0["yhat_upper"]):
        assert _rel_present(v, allowed), v


def test_evidence_allowed_numbers_are_sorted_and_unique() -> None:
    target, _ = _target_result()
    bundle = build_evidence_bundle(tenant_id="acme", finding_id=uuid4(), target=target)
    allowed = bundle.allowed_numbers
    assert allowed == sorted(allowed)
    assert len(allowed) == len(set(allowed))
    assert len(allowed) > 0


def test_build_evidence_bundle_is_deterministic() -> None:
    target, _ = _target_result()
    fid, bid = uuid4(), uuid4()
    created = datetime(2026, 6, 19, tzinfo=timezone.utc)
    kwargs = dict(
        tenant_id="acme", finding_id=fid, target=target, bundle_id=bid, created_at=created
    )
    b1 = build_evidence_bundle(**kwargs)
    b2 = build_evidence_bundle(**kwargs)
    assert b1.model_dump() == b2.model_dump()
