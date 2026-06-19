"""L3 forecasting — a single ETS model producing a point + prediction-interval band.

The MVP ships *one* forecasting model used only to draw the dashboard forecast band
(architecture §5.3, §9). Per the build constraints this is **statsmodels** ETS
(Exponential Smoothing) — not statsforecast/Prophet/numba, which are heavy and slow
on Windows. :func:`~edis_intelligence.forecast.ets_model.forecast_series` fits an
additive Holt-Winters / ETS model and emits a :class:`edis_contracts.findings.Forecast`
with ``points=[{ts, yhat, yhat_lower, yhat_upper}]`` and ``model="statsmodels.ETS"``.

Pure + deterministic + infrastructure-free: importing pulls in numpy / pandas /
statsmodels only; no DB, broker, or API key.
"""

from __future__ import annotations

from edis_intelligence.forecast.ets_model import (
    ForecastResult,
    fit_ets,
    forecast_series,
)

__all__ = ["ForecastResult", "fit_ets", "forecast_series"]
