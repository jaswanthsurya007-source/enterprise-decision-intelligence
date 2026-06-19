"""X4 detector tests -- robust z-score + STL flag a known level shift; precision/recall.

Locks the X1 detection contract (architecture §5.3 "classical first"):

* The **STL** detector flags a sustained, deseasonalized LEVEL_SHIFT on the §9
  EMEA-web revenue drop (~$95K -> ~$61K) with a large residual-sigma score, and
  reports headline numbers near the deseasonalized levels a human reads.
* The **robust z-score** detector flags an injected point spike but does NOT flag a
  clean continuation (median/MAD baseline excludes the eval window).
* On a battery of injected-anomaly fixtures -- a revenue drop, a revenue spike, an
  ops latency spike, an error-rate spike, AND a clean (no-anomaly) series that MUST
  stay silent -- the detectors achieve perfect precision and recall (no false
  positive on the clean series, no false negative on the injected ones).

All pure / deterministic / infra-free (no Docker, no API keys).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from edis_contracts.findings import FindingKind

from edis_intelligence.detectors.base import DetectionContext
from edis_intelligence.detectors.robust_zscore import RobustZScoreDetector
from edis_intelligence.detectors.stl_seasonal import StlSeasonalDetector

# Reuse the conftest's deterministic builders (same daily-series shape as L1->L2).
from edis_l3_testkit import (  # type: ignore[import-not-found]
    DEMO_START,
    DEMO_WEEKLY,
    build_clean_series,
    build_demo_series,
)


def _ctx(metric_key: str, direction: str = "both", **over) -> DetectionContext:
    return DetectionContext(
        tenant_id="acme",
        metric_key=metric_key,
        dimensions={"region": "EMEA"},
        direction=direction,
        **over,
    )


# ---------------------------------------------------------------------------
# STL -- the level shift
# ---------------------------------------------------------------------------
def test_stl_flags_known_level_shift_on_revenue_drop() -> None:
    rev, _, _ = build_demo_series()
    results = StlSeasonalDetector().detect(rev, _ctx("revenue", "down"))

    assert len(results) == 1, results
    r = results[0]
    assert r.kind is FindingKind.LEVEL_SHIFT
    assert r.detector == "stl_seasonal"
    # The drop is real and large: observed well below expected, many residual sigmas.
    assert r.deviation < 0
    assert r.deviation_pct < -25.0, r.deviation_pct
    assert r.score >= 3.5, r.score  # clears the default level_shift_k threshold
    # Headline numbers land near the deseasonalized §9 levels (~$61K vs ~$95K).
    assert 45_000.0 <= r.observed_value <= 75_000.0, r.observed_value
    assert 80_000.0 <= r.expected_value <= 110_000.0, r.expected_value
    # Sustained: the trailing run is at least the min_shift_run.
    assert r.diagnostics["shift_run"] >= 3.0


def test_stl_silent_on_clean_seasonal_series() -> None:
    clean = build_clean_series()
    results = StlSeasonalDetector().detect(clean, _ctx("revenue", "down"))
    assert results == [], results


def test_stl_needs_two_full_periods() -> None:
    # Only 10 daily points < 2*7 -> degrade to no detection (never raise).
    short = [(DEMO_START + timedelta(days=i), 100.0 + i) for i in range(10)]
    assert StlSeasonalDetector(period=7).detect(short, _ctx("revenue")) == []


# ---------------------------------------------------------------------------
# Robust z-score -- the point anomaly
# ---------------------------------------------------------------------------
def test_robust_zscore_flags_injected_point_spike() -> None:
    rng = np.random.default_rng(11)
    pts: list[tuple[datetime, float]] = []
    for i in range(30):
        d = DEMO_START + timedelta(days=i)
        pts.append((d, 200.0 + rng.normal(0, 5)))
    # Inject a single huge spike on the last day (the eval window).
    pts[-1] = (pts[-1][0], 2_000.0)

    results = RobustZScoreDetector(eval_window=3).detect(pts, _ctx("latency_p95", "up"))
    assert results, "the injected spike must be flagged"
    spike = max(results, key=lambda r: abs(r.score))
    assert spike.kind is FindingKind.POINT_ANOMALY
    assert spike.observed_value == 2_000.0
    assert abs(spike.score) >= 3.5
    assert spike.deviation > 0  # it spiked up


def test_robust_zscore_silent_on_flat_continuation() -> None:
    rng = np.random.default_rng(3)
    pts = [(DEMO_START + timedelta(days=i), 200.0 + rng.normal(0, 4)) for i in range(30)]
    results = RobustZScoreDetector(eval_window=3).detect(pts, _ctx("latency_p95", "up"))
    assert results == [], results


def test_robust_zscore_baseline_excludes_eval_window() -> None:
    # A sustained shift in only the eval window must not poison its own baseline:
    # the baseline median/MAD comes from the leading region, so the shifted tail is
    # still measured against the healthy level and flagged.
    rng = np.random.default_rng(5)
    pts: list[tuple[datetime, float]] = []
    for i in range(30):
        d = DEMO_START + timedelta(days=i)
        v = 100.0 + rng.normal(0, 2)
        if i >= 27:  # last 3 days jump to a new level
            v += 50.0
        pts.append((d, v))
    results = RobustZScoreDetector(eval_window=3).detect(pts, _ctx("error_rate", "up"))
    assert len(results) >= 1
    for r in results:
        assert r.observed_value > 140.0  # the shifted tail
        assert r.expected_value < 110.0  # baseline median, not polluted


# ---------------------------------------------------------------------------
# Precision / recall over a battery of injected fixtures (incl. a clean series)
# ---------------------------------------------------------------------------
def _seasonal(level: float, days: int, seed: int) -> list[tuple[datetime, float]]:
    rng = np.random.default_rng(seed)
    out = []
    for i in range(days):
        d = DEMO_START + timedelta(days=i)
        out.append((d, level * DEMO_WEEKLY[d.weekday()] + rng.normal(0, level * 0.012)))
    return out


def _inject_level(series, factor: float, from_idx: int):
    return [(t, v * factor if i >= from_idx else v) for i, (t, v) in enumerate(series)]


def _detected(metric_key: str, series, direction: str) -> bool:
    """Run the metric's natural detector; True iff anything is flagged."""

    if metric_key in {"revenue", "orders", "page_views"}:
        det = StlSeasonalDetector()
    else:
        det = RobustZScoreDetector(eval_window=7)
    return bool(det.detect(series, _ctx(metric_key, direction)))


def test_detector_precision_recall_on_injected_fixtures() -> None:
    n = 35
    cut = 28
    fixtures: list[tuple[str, list, str, bool]] = []

    # --- positives (anomaly injected) ---
    rev_drop = _inject_level(_seasonal(95_000.0, n, 1), 0.64, cut)
    fixtures.append(("revenue", rev_drop, "down", True))

    rev_spike = _inject_level(_seasonal(95_000.0, n, 2), 1.45, cut)
    fixtures.append(("revenue", rev_spike, "up", True))

    # ops latency spike (point-anomaly detector) in the eval window
    rng = np.random.default_rng(8)
    lat = [(DEMO_START + timedelta(days=i), 180.0 + rng.normal(0, 8)) for i in range(n)]
    lat = [(t, (1400.0 if i >= cut else v)) for i, (t, v) in enumerate(lat)]
    fixtures.append(("latency_p95", lat, "up", True))

    # error-rate spike
    rng = np.random.default_rng(9)
    err = [(DEMO_START + timedelta(days=i), 0.004 + rng.normal(0, 0.0008)) for i in range(n)]
    err = [(t, (0.09 if i >= cut else v)) for i, (t, v) in enumerate(err)]
    fixtures.append(("error_rate", err, "up", True))

    # --- negatives (clean -- MUST NOT flag) ---
    fixtures.append(("revenue", _seasonal(95_000.0, n, 100), "down", False))
    rng = np.random.default_rng(101)
    clean_lat = [(DEMO_START + timedelta(days=i), 180.0 + rng.normal(0, 8)) for i in range(n)]
    fixtures.append(("latency_p95", clean_lat, "up", False))

    tp = fp = fn = tn = 0
    for metric_key, series, direction, is_anom in fixtures:
        flagged = _detected(metric_key, series, direction)
        if is_anom and flagged:
            tp += 1
        elif is_anom and not flagged:
            fn += 1
        elif not is_anom and flagged:
            fp += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    # No false positives on the clean fixtures; no missed injected anomalies.
    assert fp == 0, f"false positive(s) on clean series; tn={tn}"
    assert fn == 0, "missed an injected anomaly"
    assert precision == 1.0
    assert recall == 1.0
