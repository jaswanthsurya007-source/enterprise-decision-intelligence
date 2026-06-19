"""Single ETS (Exponential Smoothing) forecast -> point + prediction-interval band.

Per the build constraints the forecasting model is **statsmodels** ETS — explicitly
*not* statsforecast / Prophet / numba (heavy and slow on Windows). We use
``statsmodels.tsa.holtwinters.ExponentialSmoothing`` (Holt-Winters) for the point
path and derive a symmetric prediction-interval band from the in-sample residual
scale and the model's normal quantile, widening with the horizon. This gives the
dashboard a believable forecast band (architecture §5.3 "band only") without the
fragile native extensions ETSModel's simulation path would pull in.

Design choices (all to keep it deterministic + robust on short demo series):

* **Seasonality is opt-in by length.** Additive weekly seasonality (period 7) is
  used only when there are at least ``2 * period`` observations; otherwise we fall
  back to additive-trend (Holt) or simple smoothing, so a short series still yields
  a band instead of raising.
* **The fit is on the pre-incident region by default.** A forecast is the
  counterfactual "where the series *should* be heading"; fitting through a fresh
  level shift would drag the projection toward the anomaly. ``fit_window`` trims the
  trailing anomalous tail (default: keep all but the last ``eval_window`` points) so
  the band reflects the healthy baseline the actuals then diverge from.
* **Band = yhat ± z * sigma * sqrt(1 + h/n).** ``sigma`` is the robust scale of the
  in-sample residuals (MAD->sigma, resilient to the trimmed tail's leakage), ``z`` is
  the normal quantile for the requested coverage, and the ``sqrt`` term widens the
  interval as the horizon grows. Lower bounds are floored at 0 for non-negative
  metrics (revenue / counts / latency / rates).

Everything is pure + deterministic; ``forecast_series`` is unit-testable on an
in-memory daily series with no infrastructure.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

import numpy as np
import pandas as pd

from edis_contracts.findings import Forecast

from edis_intelligence.detectors.base import (
    MAD_TO_SIGMA,
    SeriesLike,
    as_series,
    median_abs_deviation,
)

#: The model string stamped on every Forecast this module produces.
MODEL_NAME = "statsmodels.ETS"

_DEFAULT_PERIOD = 7
_DEFAULT_HORIZON = 7
_DEFAULT_INTERVAL = 0.95


@dataclass(frozen=True)
class ForecastResult:
    """In-memory forecast: aligned future timestamps + point/band arrays."""

    timestamps: list[datetime]
    yhat: list[float]
    yhat_lower: list[float]
    yhat_upper: list[float]
    model: str
    sigma: float
    period_used: int
    diagnostics: dict[str, float]

    def to_points(self) -> list[dict]:
        """Render to the ``Forecast.points`` shape: ``[{ts, yhat, yhat_lower, yhat_upper}]``."""

        return [
            {
                "ts": ts.isoformat(),
                "yhat": round(float(y), 6),
                "yhat_lower": round(float(lo), 6),
                "yhat_upper": round(float(hi), 6),
            }
            for ts, y, lo, hi in zip(self.timestamps, self.yhat, self.yhat_lower, self.yhat_upper)
        ]


def _z_for_interval(interval: float) -> float:
    """Two-sided normal quantile for a coverage ``interval`` (e.g. 0.95 -> ~1.96)."""

    interval = min(max(float(interval), 0.50), 0.9999)
    p = 1.0 - (1.0 - interval) / 2.0
    # Acklam's rational approximation to the inverse normal CDF (pure, deterministic).
    a = [
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    ]
    b = [
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    ]
    d = [
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    ]
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    elif p <= phigh:
        q = p - 0.5
        r = q * q
        x = (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
            * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
        )
    else:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    return float(x)


def _future_index(index: pd.DatetimeIndex, horizon: int) -> list[datetime]:
    """Step the series' inferred cadence ``horizon`` points beyond its last ts."""

    if len(index) >= 2:
        # Resolution-safe step: reduce over real Timedelta diffs (a raw int64 view of
        # a DatetimeIndex is not always nanoseconds under pandas' new resolutions).
        diffs = index.to_series().diff().dropna()
        step = diffs.median() if not diffs.empty else pd.Timedelta(days=1)
        if not isinstance(step, pd.Timedelta) or step <= pd.Timedelta(0):
            step = pd.Timedelta(days=1)
    else:
        step = pd.Timedelta(days=1)
    last = index[-1]
    return [(last + step * (i + 1)).to_pydatetime() for i in range(horizon)]


def fit_ets(
    series: SeriesLike,
    *,
    horizon: int = _DEFAULT_HORIZON,
    interval: float = _DEFAULT_INTERVAL,
    period: int = _DEFAULT_PERIOD,
    fit_window: int | None = None,
    eval_window: int = 7,
    non_negative: bool = True,
) -> ForecastResult:
    """Fit a statsmodels ETS/Holt-Winters model and project ``horizon`` points + band.

    ``fit_window`` (if given) keeps only the trailing ``fit_window`` points for the
    fit; otherwise the trailing ``eval_window`` (anomalous) points are trimmed so the
    forecast reflects the healthy baseline. Seasonality (additive, ``period``) is used
    only when enough history supports it, degrading to trend-only / simple smoothing.
    Returns a :class:`ForecastResult`; pure aside from statsmodels' deterministic fit.
    """

    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    s = as_series(series)
    full_index = s.index

    # Choose the fit region.
    if fit_window is not None:
        fit_s = s.iloc[-int(fit_window) :]
    elif eval_window > 0 and len(s) > eval_window + 2 * period:
        fit_s = s.iloc[: len(s) - eval_window]
    else:
        fit_s = s

    values = fit_s.to_numpy(dtype=float)
    n = len(values)

    use_seasonal = n >= 2 * period and period >= 2
    use_trend = n >= 4

    seasonal = "add" if use_seasonal else None
    trend = "add" if use_trend else None
    sp = period if use_seasonal else None

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            model = ExponentialSmoothing(
                values,
                trend=trend,
                seasonal=seasonal,
                seasonal_periods=sp,
                initialization_method="estimated",
            )
            fit = model.fit(optimized=True)
            point = np.asarray(fit.forecast(horizon), dtype=float)
            resid = values - np.asarray(fit.fittedvalues, dtype=float)
        except Exception:
            # Robust fallback: flat last-level forecast (never block the band).
            level = float(values[-1]) if n else 0.0
            point = np.full(horizon, level, dtype=float)
            resid = values - (np.full(n, float(np.median(values))) if n else np.zeros(0))
            use_seasonal = False
            sp = 0

    # Robust residual scale (MAD->sigma), resilient to any leaked tail effect.
    if len(resid) >= 2:
        _med, mad = median_abs_deviation(resid)
        sigma = mad * MAD_TO_SIGMA
        if sigma <= 0.0:
            sigma = float(np.std(resid)) or max(abs(float(np.mean(values))) * 1e-3, 1e-9)
    else:
        sigma = max(abs(float(np.mean(values))) if n else 1.0, 1.0) * 1e-3

    z = _z_for_interval(interval)
    lower: list[float] = []
    upper: list[float] = []
    for h, y in enumerate(point, start=1):
        widen = math.sqrt(1.0 + h / max(n, 1))
        half = z * sigma * widen
        lo = y - half
        hi = y + half
        if non_negative and lo < 0.0:
            lo = 0.0
        lower.append(float(lo))
        upper.append(float(hi))

    timestamps = _future_index(full_index, horizon)

    return ForecastResult(
        timestamps=timestamps,
        yhat=[float(v) for v in point],
        yhat_lower=lower,
        yhat_upper=upper,
        model=MODEL_NAME,
        sigma=float(sigma),
        period_used=int(sp or 0),
        diagnostics={
            "fit_n": float(n),
            "residual_sigma": float(sigma),
            "interval": float(interval),
            "z": float(z),
            "seasonal": 1.0 if use_seasonal else 0.0,
            "horizon": float(horizon),
        },
    )


def forecast_series(
    series: SeriesLike,
    *,
    tenant_id: str,
    metric_key: str,
    dimensions: dict[str, str] | None = None,
    horizon_days: int = _DEFAULT_HORIZON,
    interval: float = _DEFAULT_INTERVAL,
    period: int = _DEFAULT_PERIOD,
    fit_window: int | None = None,
    eval_window: int = 7,
    non_negative: bool = True,
    forecast_id: UUID | None = None,
    generated_at: datetime | None = None,
) -> Forecast:
    """Forecast ``series`` and return a :class:`edis_contracts.findings.Forecast`.

    The single MVP band: ETS point + prediction interval over ``horizon_days``,
    keyed by ``tenant_id`` / ``metric_key`` / ``dimensions`` for the
    ``edis.forecasts.v1`` topic. ``model`` is ``"statsmodels.ETS"`` (the real library
    used). Pure / deterministic; testable on an in-memory series.
    """

    res = fit_ets(
        series,
        horizon=horizon_days,
        interval=interval,
        period=period,
        fit_window=fit_window,
        eval_window=eval_window,
        non_negative=non_negative,
    )
    return Forecast(
        forecast_id=forecast_id or uuid4(),
        tenant_id=tenant_id,
        metric_key=metric_key,
        dimensions=dict(dimensions or {}),
        model=res.model,
        horizon_days=int(horizon_days),
        points=res.to_points(),
        generated_at=generated_at or datetime.now(timezone.utc),
    )
