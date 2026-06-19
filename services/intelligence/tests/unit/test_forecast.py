"""X4 forecast tests -- the single ETS band brackets a stable series; deterministic.

Locks the X2 forecast contract (architecture §5.3 "band only"):

* the model string is the REAL library used (``statsmodels.ETS``), never
  statsforecast/Prophet;
* on a STABLE weekly-seasonal series the held-out actual falls within the
  prediction band the model would draw for that horizon (the band brackets the
  truth) -- so the dashboard's "actual vs forecast band" is meaningful;
* the band is non-negative-floored and widens with the horizon;
* the forecast is byte-deterministic for a fixed input (no RNG, no network).

All pure / deterministic / infra-free.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np

from edis_contracts.findings import Forecast

from edis_intelligence.forecast.ets_model import MODEL_NAME, fit_ets, forecast_series

from edis_l3_testkit import DEMO_START, DEMO_WEEKLY  # type: ignore[import-not-found]


def _stable_seasonal(days: int, level: float = 95_000.0, seed: int = 21) -> list:
    rng = np.random.default_rng(seed)
    return [
        (
            DEMO_START + timedelta(days=i),
            level * DEMO_WEEKLY[(DEMO_START + timedelta(days=i)).weekday()]
            + rng.normal(0, level * 0.01),
        )
        for i in range(days)
    ]


def test_model_string_is_real_library() -> None:
    fc = forecast_series(
        _stable_seasonal(40), tenant_id="acme", metric_key="revenue", horizon_days=7
    )
    assert isinstance(fc, Forecast)
    assert fc.model == MODEL_NAME == "statsmodels.ETS"


def test_band_brackets_actual_on_stable_series() -> None:
    # Build a long stable series; hold out the last `horizon` days as "actuals" and
    # fit on the head only, then assert each held-out actual lands inside the band.
    horizon = 7
    full = _stable_seasonal(40 + horizon)
    train = full[:-horizon]
    actuals = [v for _t, v in full[-horizon:]]

    # fit_window=None with eval_window=0 means "fit on all of train" (no trim) -- the
    # train series is healthy, so the band should bracket the held-out continuation.
    res = fit_ets(train, horizon=horizon, interval=0.95, period=7, eval_window=0)
    assert res.model == "statsmodels.ETS"
    assert len(res.yhat) == horizon

    inside = 0
    for a, lo, hi in zip(actuals, res.yhat_lower, res.yhat_upper):
        assert lo <= hi
        if lo <= a <= hi:
            inside += 1
    # A 95% band over 7 stable points must bracket the large majority of actuals.
    assert inside >= horizon - 1, (inside, list(zip(actuals, res.yhat_lower, res.yhat_upper)))


def test_band_non_negative_and_widens_with_horizon() -> None:
    fc = forecast_series(
        _stable_seasonal(40),
        tenant_id="acme",
        metric_key="revenue",
        dimensions={"region": "EMEA"},
        horizon_days=7,
    )
    assert len(fc.points) == 7
    for p in fc.points:
        assert set(p) == {"ts", "yhat", "yhat_lower", "yhat_upper"}
        assert p["yhat_lower"] <= p["yhat"] <= p["yhat_upper"]
        assert p["yhat_lower"] >= 0.0  # non-negative metric floored
    w0 = fc.points[0]["yhat_upper"] - fc.points[0]["yhat_lower"]
    wN = fc.points[-1]["yhat_upper"] - fc.points[-1]["yhat_lower"]
    assert wN >= w0


def test_forecast_is_deterministic() -> None:
    series = _stable_seasonal(40)
    a = forecast_series(
        series, tenant_id="acme", metric_key="revenue", horizon_days=7, forecast_id=None
    )
    b = forecast_series(
        series, tenant_id="acme", metric_key="revenue", horizon_days=7, forecast_id=None
    )
    # forecast_id / generated_at differ (uuid/now) but the computed band is identical
    assert a.points == b.points
    assert a.model == b.model and a.horizon_days == b.horizon_days


def test_short_series_still_bands_without_raising() -> None:
    days = [DEMO_START + timedelta(days=i) for i in range(6)]
    short = [(d, 100.0 + i) for i, d in enumerate(days)]
    fc = forecast_series(short, tenant_id="acme", metric_key="orders", horizon_days=3)
    assert len(fc.points) == 3
    assert fc.model == "statsmodels.ETS"
