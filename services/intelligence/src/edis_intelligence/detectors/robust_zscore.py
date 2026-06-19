"""Robust z-score point-anomaly detector (median / MAD).

The classic Iglewicz/Hoaglin "modified z-score": for each point ``x`` in the
series, ``z = 0.6745 * (x - median) / MAD``, where ``median``/``MAD`` are computed
over a robust **baseline** (the leading window, excluding the candidate tail).
Using the median + MAD instead of mean + stddev makes the baseline itself robust
to the very outliers we are trying to detect (50% breakdown point), so a few
anomalous days don't inflate the threshold and mask themselves.

A point whose ``|z|`` exceeds ``z_threshold`` (default 3.5) is flagged as a
:attr:`FindingKind.POINT_ANOMALY`. ``expected_value`` is the baseline median and
``score`` is the (signed-magnitude) robust z. The detector is pure + deterministic
and runs on the in-memory daily series the L2 rollup produces.

When MAD is zero (a perfectly flat baseline) the detector falls back to a tiny
epsilon derived from the baseline scale so a genuine departure from a flat line is
still flagged, while a flat continuation produces ``z == 0`` (no false positive).
"""

from __future__ import annotations

import numpy as np

from edis_contracts.findings import FindingKind

from edis_intelligence.detectors.base import (
    MAD_TO_SIGMA,
    DetectionContext,
    DetectorResult,
    SeriesLike,
    as_series,
    median_abs_deviation,
)

_DEFAULT_Z_THRESHOLD = 3.5
#: Floor for the robust scale when MAD == 0, as a fraction of |median| (or 1.0).
_FLAT_SCALE_FRACTION = 1e-3


class RobustZScoreDetector:
    """Median/MAD modified z-score detector for point anomalies."""

    name = "robust_zscore"
    version = "1.0"

    def __init__(
        self,
        *,
        z_threshold: float = _DEFAULT_Z_THRESHOLD,
        baseline_window: int | None = None,
        eval_window: int = 7,
    ) -> None:
        """Construct the detector.

        ``z_threshold``: modified-z magnitude above which a point is flagged.
        ``baseline_window``: number of leading points used to form the robust
        median/MAD baseline; ``None`` means "all points before the eval window".
        ``eval_window``: number of trailing points scored against the baseline
        (the candidate/incident region). The baseline always *excludes* the eval
        window so the points under test cannot pollute their own expectation.
        """

        self.z_threshold = float(z_threshold)
        self.baseline_window = baseline_window
        self.eval_window = int(eval_window)

    def detect(self, series: SeriesLike, ctx: DetectionContext) -> list[DetectorResult]:
        s = as_series(series)
        z_threshold = float(ctx.z_threshold) if ctx.z_threshold is not None else self.z_threshold

        n = len(s)
        if n < 2:
            return []

        eval_n = min(self.eval_window, n - 1)  # leave >= 1 point for the baseline
        eval_n = max(eval_n, 1)
        baseline = s.iloc[: n - eval_n]
        if self.baseline_window is not None:
            baseline = baseline.iloc[-self.baseline_window :]
        if len(baseline) < 1:
            return []

        median, mad = median_abs_deviation(baseline.to_numpy())
        sigma = mad * MAD_TO_SIGMA
        if sigma <= 0.0:
            # Flat baseline: use a tiny scale so a real departure is still caught,
            # but a flat continuation yields z == 0 (no spurious flag).
            sigma = max(abs(median), 1.0) * _FLAT_SCALE_FRACTION

        results: list[DetectorResult] = []
        eval_slice = s.iloc[n - eval_n :]
        for ts, value in eval_slice.items():
            z = (float(value) - median) / sigma
            if abs(z) < z_threshold:
                continue
            deviation = float(value) - median
            deviation_pct = (deviation / median * 100.0) if median != 0 else 0.0
            results.append(
                DetectorResult(
                    detector=self.name,
                    detector_version=self.version,
                    kind=FindingKind.POINT_ANOMALY,
                    metric_key=ctx.metric_key,
                    dimensions=dict(ctx.dimensions),
                    window_start=ts.to_pydatetime(),
                    window_end=ts.to_pydatetime(),
                    observed_value=float(value),
                    expected_value=float(median),
                    deviation=float(deviation),
                    deviation_pct=float(deviation_pct),
                    score=float(z),
                    diagnostics={
                        "robust_z": float(z),
                        "baseline_median": float(median),
                        "baseline_mad": float(mad),
                        "baseline_sigma": float(sigma),
                        "baseline_n": float(len(baseline)),
                    },
                )
            )
        return results

    # Convenience: score every point (not just the eval window) — useful for the
    # evidence bundler / tests that want the full z-series. Not part of the
    # Detector protocol; pure.
    def score_all(self, series: SeriesLike, ctx: DetectionContext) -> "np.ndarray":
        s = as_series(series)
        median, mad = median_abs_deviation(s.to_numpy())
        sigma = mad * MAD_TO_SIGMA
        if sigma <= 0.0:
            sigma = max(abs(median), 1.0) * _FLAT_SCALE_FRACTION
        return (s.to_numpy() - median) / sigma
