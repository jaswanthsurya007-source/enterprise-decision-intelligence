"""STL seasonal-decomposition detector — sustained LEVEL_SHIFT detection.

Uses :class:`statsmodels.tsa.seasonal.STL` to decompose a daily metric series into
``trend + seasonal + residual``. Weekly-seasonal business series (revenue, orders)
have a strong day-of-week pattern; STL removes it so a genuine *level* change is
not hidden by ordinary weekly variation.

Detection logic (pure, deterministic):

1. Decompose with ``period = stl_period`` (default 7 for daily-weekly).
2. Compute the robust scale of the **residual** (MAD -> sigma). The residual is
   what's left after trend + seasonality, so its MAD is the natural "normal noise"
   yardstick.
3. Form the **baseline trend level** as the median of the trend over the leading
   (pre-incident) region, and the **observed level** as the median of the trend
   over the trailing ``eval_window`` (the candidate incident region).
4. The level shift is ``observed_level - baseline_level``. Express it in residual
   sigmas: ``score = |shift| / residual_sigma``. Flag a :attr:`FindingKind.LEVEL_SHIFT`
   when ``|shift| > level_shift_k * residual_MAD`` **and** the shift is *sustained*
   — i.e. at least ``min_shift_run`` consecutive trailing points are deseasonalized
   to the shifted side of the baseline level. The "sustained" guard is what
   separates a level shift from a one-day spike (which robust z-score owns).

The demo target — EMEA-web daily revenue dropping ~36% (from ~$95K to ~$61K) — is
a large, sustained, deseasonalized step and trips this detector with a high score
(many residual sigmas), exactly as architecture §9 requires.

``observed_value`` / ``expected_value`` are reported as the **observed-window mean
of the raw series** and the **baseline expectation reconstructed from STL** (the
baseline trend level plus the eval-window's average seasonal component), so the
Finding's headline numbers are the deseasonalized levels a human reads as "$61K vs
$95K" rather than raw STL trend internals.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from edis_contracts.findings import FindingKind

from edis_intelligence.detectors.base import (
    MAD_TO_SIGMA,
    DetectionContext,
    DetectorResult,
    SeriesLike,
    as_series,
    median_abs_deviation,
)

_DEFAULT_PERIOD = 7
_DEFAULT_LEVEL_SHIFT_K = 3.5
_DEFAULT_MIN_SHIFT_RUN = 3


class StlSeasonalDetector:
    """STL-residual based detector for sustained level shifts (and seasonal breaks)."""

    name = "stl_seasonal"
    version = "1.0"

    def __init__(
        self,
        *,
        period: int = _DEFAULT_PERIOD,
        level_shift_k: float = _DEFAULT_LEVEL_SHIFT_K,
        min_shift_run: int = _DEFAULT_MIN_SHIFT_RUN,
        eval_window: int = 7,
        robust: bool = True,
    ) -> None:
        """Construct the detector.

        ``period``: STL seasonal period in samples (7 for daily-weekly).
        ``level_shift_k``: trend-shift magnitude (in residual MADs) above which a
        sustained shift is flagged.
        ``min_shift_run``: minimum consecutive trailing points on the shifted side
        for the change to count as a level shift (vs a transient).
        ``eval_window``: trailing points treated as the candidate incident region.
        ``robust``: pass STL's robust (Loess re-weighting) flag so the fit itself
        is resistant to the anomaly.
        """

        self.period = int(period)
        self.level_shift_k = float(level_shift_k)
        self.min_shift_run = int(min_shift_run)
        self.eval_window = int(eval_window)
        self.robust = bool(robust)

    def detect(self, series: SeriesLike, ctx: DetectionContext) -> list[DetectorResult]:
        # Lazy import keeps base import cheap and avoids importing statsmodels
        # until a detector actually runs.
        from statsmodels.tsa.seasonal import STL

        s = as_series(series)
        period = int(ctx.stl_period) if ctx.stl_period is not None else self.period
        level_shift_k = (
            float(ctx.level_shift_k) if ctx.level_shift_k is not None else self.level_shift_k
        )
        min_shift_run = (
            int(ctx.min_shift_run) if ctx.min_shift_run is not None else self.min_shift_run
        )

        n = len(s)
        # STL needs at least two full periods to separate trend from seasonality.
        if period < 2 or n < 2 * period:
            return []

        eval_n = min(self.eval_window, n - period)  # keep >= one period as baseline
        eval_n = max(eval_n, 1)

        values = s.to_numpy(dtype=float)
        stl = STL(values, period=period, robust=self.robust)
        res = stl.fit()
        trend = np.asarray(res.trend, dtype=float)
        seasonal = np.asarray(res.seasonal, dtype=float)
        resid = np.asarray(res.resid, dtype=float)

        # Robust residual scale = the "normal noise" yardstick.
        _resid_med, resid_mad = median_abs_deviation(resid)
        resid_sigma = resid_mad * MAD_TO_SIGMA
        if resid_sigma <= 0.0:
            # Degenerate (perfectly clean) residual: fall back to the trend's own
            # scale so a real step is still expressible in "sigmas".
            _t_med, t_mad = median_abs_deviation(np.diff(trend))
            resid_sigma = max(t_mad * MAD_TO_SIGMA, 1e-9)
            resid_mad = resid_sigma * 0.6745

        baseline_trend = trend[: n - eval_n]
        eval_trend = trend[n - eval_n :]
        baseline_level = float(np.median(baseline_trend))
        observed_level = float(np.median(eval_trend))
        shift = observed_level - baseline_level

        # "Sustained": count trailing deseasonalized points on the shifted side of
        # the baseline level. Deseasonalize as value - seasonal so day-of-week
        # variation doesn't break the run.
        deseasonalized = values - seasonal
        sign = 1.0 if shift >= 0 else -1.0
        run = 0
        for i in range(n - 1, -1, -1):
            if sign * (deseasonalized[i] - baseline_level) > 0:
                run += 1
            else:
                break

        score = abs(shift) / resid_sigma if resid_sigma > 0 else 0.0
        flagged = abs(shift) > level_shift_k * resid_mad and run >= min_shift_run
        if not flagged:
            return []

        # Headline numbers: deseasonalized levels a human recognizes. Observed is
        # the eval window's deseasonalized mean; expected is the baseline level
        # plus the eval window's mean seasonal component (so it's comparable to the
        # raw observed mean, not a trend-only abstraction).
        observed_value = float(np.mean(deseasonalized[n - eval_n :]))
        eval_seasonal_mean = float(np.mean(seasonal[n - eval_n :]))
        expected_value = float(baseline_level + eval_seasonal_mean)
        # Reconcile observed_value to the same (level + seasonal) basis so the
        # reported deviation equals the detected trend shift.
        observed_value = float(observed_level + eval_seasonal_mean)
        deviation = observed_value - expected_value
        deviation_pct = (deviation / expected_value * 100.0) if expected_value != 0 else 0.0

        window_start = s.index[n - eval_n].to_pydatetime()
        window_end = s.index[-1].to_pydatetime()

        return [
            DetectorResult(
                detector=self.name,
                detector_version=self.version,
                kind=FindingKind.LEVEL_SHIFT,
                metric_key=ctx.metric_key,
                dimensions=dict(ctx.dimensions),
                window_start=window_start,
                window_end=window_end,
                observed_value=observed_value,
                expected_value=expected_value,
                deviation=float(deviation),
                deviation_pct=float(deviation_pct),
                score=float(score),
                diagnostics={
                    "level_shift": float(shift),
                    "baseline_level": float(baseline_level),
                    "observed_level": float(observed_level),
                    "residual_sigma": float(resid_sigma),
                    "residual_mad": float(resid_mad),
                    "shift_run": float(run),
                    "stl_period": float(period),
                    "eval_window": float(eval_n),
                },
            )
        ]

    def decompose(self, series: SeriesLike) -> pd.DataFrame:
        """Return the STL ``trend``/``seasonal``/``resid`` frame (helper for X2/tests)."""

        from statsmodels.tsa.seasonal import STL

        s = as_series(series)
        res = STL(s.to_numpy(dtype=float), period=self.period, robust=self.robust).fit()
        return pd.DataFrame(
            {"trend": res.trend, "seasonal": res.seasonal, "resid": res.resid},
            index=s.index,
        )
