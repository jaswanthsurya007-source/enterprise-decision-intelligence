"""Lag-aware cross-correlation RCA — rank candidate drivers of an anomaly.

Given a *target* anomalous series (the metric a detector flagged, e.g. EMEA-web
``revenue``) and a set of *candidate driver* series (e.g. EMEA ``checkout-api``
``latency_p95`` / ``error_rate``), this module finds, for each driver, the time lag
at which it best explains the target and ranks the drivers by the strength of that
lagged relationship.

Why lag-aware
-------------
A cause precedes its effect. The ops outage (latency / error spike) *leads* the
revenue level shift by hours. A plain (lag-0) correlation would understate the
relationship; scanning a window of lags and keeping the lag of peak |correlation|
both (a) strengthens the signal and (b) recovers the **direction** — a driver whose
best lag is negative (driver *earlier* than target) is ``leading`` (a candidate
cause); lag ~0 is ``coincident``; positive is ``lagging`` (likely an effect, not a
cause).

The math (pure, deterministic)
-------------------------------
1. Align the target and each candidate onto one common, regularly-spaced time grid
   (the union of timestamps; we infer the sampling step from the target so a daily
   series stays daily). Missing points are linearly interpolated then mean-filled,
   so a candidate sampled differently still aligns.
2. Correlate the **aligned levels** by default (``difference=False``). An incident is
   a *sustained* co-movement — an ops outage holds latency/errors high for days while
   revenue stays depressed — so the level series move together over the whole window
   and Pearson on the aligned levels is exactly the right, strong signal (the demo's
   latency↑/error↑ vs revenue↓ is a large **negative** level correlation). First
   differencing (``difference=True``) is offered for the *other* regime — two slowly
   *trending* series, where Pearson on raw levels is famously spurious and the guard
   is to correlate changes — but it is **not** the default here because differencing
   collapses a clean step/level-shift incident into a single impulse and destroys the
   very co-occurrence RCA is trying to surface. The lag scan (step 3) recovers
   precedence in either mode.
3. For each integer lag ``k`` in ``[-max_lag, +max_lag]`` (in *samples*), shift the
   candidate by ``k`` and compute the Pearson correlation of the overlapping region.
   Keep the lag of maximum |correlation|. ``lag_minutes`` is that lag times the grid
   step; a *negative* sample lag (candidate leads target) yields a *positive*
   reported ``lag_minutes`` magnitude with ``direction="leading"`` — i.e. "the driver
   moved N minutes *before* the target."
4. ``observed_delta`` for the candidate is its own (incident-window mean − baseline
   mean) on the raw series, so it is a real, citable figure.

Sign handling: the returned ``correlation`` is the *signed* best-lag Pearson r in
``[-1, 1]``. The demo's latency↑/error↑ vs revenue↓ relationship is a strong
**negative** level correlation. RCA ranks by ``|correlation|`` (explanatory strength
regardless of sign) but reports the signed value so the narrator/decision layer can
read the direction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np
import pandas as pd

from edis_contracts.findings import CandidateCause

from edis_intelligence.detectors.base import SeriesLike, as_series


@dataclass(frozen=True)
class Candidate:
    """A candidate driver series + the identity to stamp on its CandidateCause."""

    metric_key: str
    dimensions: dict[str, str]
    series: SeriesLike


@dataclass(frozen=True)
class LaggedCorrelation:
    """The best-lag cross-correlation of one candidate against the target.

    ``lag_samples`` is the candidate's lead/lag in grid steps at peak |r|:
    *negative* = candidate leads the target (a cause); *positive* = candidate lags.
    ``lag_minutes`` is the absolute lag magnitude in minutes (always >= 0).
    ``direction`` is derived from the sign of ``lag_samples`` with a coincidence band.
    """

    metric_key: str
    dimensions: dict[str, str]
    correlation: float
    lag_samples: int
    lag_minutes: int
    direction: Literal["leading", "coincident", "lagging"]
    observed_delta: float
    #: Computed diagnostics, all floats (joinable to allowed_numbers).
    diagnostics: dict[str, float]


# ---------------------------------------------------------------------------
# Grid alignment
# ---------------------------------------------------------------------------
def _infer_step(index: pd.DatetimeIndex) -> pd.Timedelta:
    """Infer a regular sampling step from a DatetimeIndex (median of diffs)."""

    if len(index) < 2:
        return pd.Timedelta(days=1)
    # Use the resolution-aware Timedelta diffs, NOT a raw int64 view: pandas may
    # back a DatetimeIndex with microsecond (or other) resolution, so int views are
    # not always nanoseconds. Reducing the median over real Timedeltas is unit-safe.
    diffs = index.to_series().diff().dropna()
    if diffs.empty:
        return pd.Timedelta(days=1)
    step = diffs.median()
    if not isinstance(step, pd.Timedelta) or step <= pd.Timedelta(0):
        return pd.Timedelta(days=1)
    return step


def _common_grid(target: pd.Series, others: Sequence[pd.Series]) -> pd.DatetimeIndex:
    """Build the regular union grid spanning all series at the target's step."""

    starts = [target.index.min()] + [o.index.min() for o in others if len(o)]
    ends = [target.index.max()] + [o.index.max() for o in others if len(o)]
    start = min(starts)
    end = max(ends)
    step = _infer_step(target.index)
    if step <= pd.Timedelta(0):
        step = pd.Timedelta(days=1)
    return pd.date_range(start=start, end=end, freq=step, tz="UTC")


def _onto_grid(s: pd.Series, grid: pd.DatetimeIndex) -> pd.Series:
    """Reindex ``s`` onto ``grid`` with time-aware interpolation + edge fill."""

    # Union-index, interpolate by time, then snap to the grid.
    union = s.index.union(grid)
    r = s.reindex(union).interpolate(method="time").reindex(grid)
    r = r.ffill().bfill()
    if r.isna().all():
        r = r.fillna(0.0)
    else:
        r = r.fillna(float(r.mean()))
    return r


def _window_mean(s: pd.Series, frac_tail: float) -> tuple[float, float]:
    """Return ``(baseline_mean, incident_mean)`` splitting the tail ``frac_tail``."""

    n = len(s)
    if n == 0:
        return 0.0, 0.0
    eval_n = max(1, int(round(n * frac_tail)))
    eval_n = min(eval_n, n - 1) if n > 1 else 1
    base = s.iloc[: n - eval_n]
    inc = s.iloc[n - eval_n :]
    base_mean = float(base.mean()) if len(base) else float(inc.mean())
    inc_mean = float(inc.mean())
    return base_mean, inc_mean


# ---------------------------------------------------------------------------
# Core cross-correlation
# ---------------------------------------------------------------------------
def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson r of two equal-length vectors; 0 on degenerate (zero-variance) input."""

    if len(a) < 2:
        return 0.0
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt(np.dot(a, a) * np.dot(b, b)))
    if denom <= 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def cross_correlate(
    target: SeriesLike,
    candidate: SeriesLike,
    *,
    max_lag: int = 5,
    difference: bool = False,
    coincident_band: int = 0,
) -> LaggedCorrelation:
    """Cross-correlate ``candidate`` against ``target`` over lags ``[-max_lag, max_lag]``.

    Returns the :class:`LaggedCorrelation` at the lag of peak |Pearson r|. A negative
    ``lag_samples`` means the candidate leads (a cause). ``difference`` differences
    both series first (co-movement of changes); ``coincident_band`` is the number of
    samples around lag 0 still treated as ``coincident``.
    """

    t = as_series(target)
    c = as_series(candidate)
    grid = _common_grid(t, [c])
    tg = _onto_grid(t, grid)
    cg = _onto_grid(c, grid)

    step = _infer_step(grid) if len(grid) >= 2 else _infer_step(t.index)
    step_minutes = max(1, int(round(step / pd.Timedelta(minutes=1))))

    tv = tg.to_numpy(dtype=float)
    cv = cg.to_numpy(dtype=float)
    if difference:
        tv = np.diff(tv)
        cv = np.diff(cv)

    n = len(tv)
    max_lag = max(0, min(int(max_lag), max(0, n - 2)))

    best_r = 0.0
    best_lag = 0
    # Scan lags ordered by increasing |k| (0, -1, +1, -2, +2, ...) and only adopt a
    # new lag when it beats the incumbent by more than a tie tolerance. This makes
    # the selection *parsimonious*: among lags whose |correlation| is effectively
    # tied (a sustained step correlates strongly across many shifts), the smallest
    # |lag| wins, so RCA reports the most plausible precedence rather than the
    # largest mechanical shift. Negative lag before positive at equal |k| so a true
    # lead is preferred over an equal-strength lag on an exact tie.
    _tie_tol = 1e-3
    lag_order = [0]
    for m in range(1, max_lag + 1):
        lag_order.extend((-m, m))
    for k in lag_order:
        # Shift candidate by k: positive k -> candidate moved later (lags target).
        # We correlate target[t] with candidate[t + k] over the overlap.
        if k >= 0:
            a = tv[: n - k] if k > 0 else tv
            b = cv[k:] if k > 0 else cv
        else:
            a = tv[-k:]
            b = cv[: n + k]
        if len(a) < 2:
            continue
        r = _pearson(a, b)
        if abs(r) > abs(best_r) + _tie_tol:
            best_r = r
            best_lag = k

    # direction from sign of lag (candidate leads target == best_lag < 0)
    if abs(best_lag) <= coincident_band:
        direction: Literal["leading", "coincident", "lagging"] = "coincident"
    elif best_lag < 0:
        direction = "leading"
    else:
        direction = "lagging"

    base_mean, inc_mean = _window_mean(c, frac_tail=0.3)
    observed_delta = inc_mean - base_mean
    lag_minutes = abs(best_lag) * step_minutes

    return LaggedCorrelation(
        metric_key=getattr(candidate, "name", None) or "",
        dimensions={},
        correlation=round(float(best_r), 6),
        lag_samples=int(best_lag),
        lag_minutes=int(lag_minutes),
        direction=direction,
        observed_delta=round(float(observed_delta), 6),
        diagnostics={
            "grid_step_minutes": float(step_minutes),
            "best_lag_samples": float(best_lag),
            "candidate_baseline_mean": round(float(base_mean), 6),
            "candidate_incident_mean": round(float(inc_mean), 6),
            "n_diff_points": float(n),
        },
    )


def rank_candidate_causes(
    target: SeriesLike,
    candidates: Sequence[Candidate],
    *,
    max_lag: int = 5,
    difference: bool = False,
    coincident_band: int = 0,
    min_abs_correlation: float = 0.3,
    leading_only: bool = False,
    top_k: int | None = None,
) -> list[CandidateCause]:
    """Rank ``candidates`` as causes of the anomalous ``target`` series.

    For each candidate, computes the best-lag :class:`LaggedCorrelation`, drops those
    below ``min_abs_correlation`` (and non-leading ones when ``leading_only``), then
    sorts by ``|correlation|`` descending (leading before lagging on ties, then larger
    ``|observed_delta|``). ``contribution_pct`` is left ``None`` here — it is assigned
    by :func:`edis_intelligence.rca.decomposition.contribution_pct_from_causes` so the
    contribution policy lives in one place.

    Returns a list of :class:`edis_contracts.findings.CandidateCause`. Pure /
    deterministic — identical input always yields identical ranking.
    """

    scored: list[LaggedCorrelation] = []
    for cand in candidates:
        lc = cross_correlate(
            target,
            cand.series,
            max_lag=max_lag,
            difference=difference,
            coincident_band=coincident_band,
        )
        lc = LaggedCorrelation(
            metric_key=cand.metric_key,
            dimensions=dict(cand.dimensions),
            correlation=lc.correlation,
            lag_samples=lc.lag_samples,
            lag_minutes=lc.lag_minutes,
            direction=lc.direction,
            observed_delta=lc.observed_delta,
            diagnostics=lc.diagnostics,
        )
        if abs(lc.correlation) < min_abs_correlation:
            continue
        if leading_only and lc.direction == "lagging":
            continue
        scored.append(lc)

    _dir_rank = {"leading": 0, "coincident": 1, "lagging": 2}
    scored.sort(
        key=lambda x: (
            -abs(x.correlation),
            _dir_rank[x.direction],
            -abs(x.observed_delta),
            x.metric_key,
        )
    )
    if top_k is not None:
        scored = scored[:top_k]

    return [
        CandidateCause(
            metric_key=lc.metric_key,
            dimensions=lc.dimensions,
            correlation=lc.correlation,
            lag_minutes=lc.lag_minutes,
            contribution_pct=None,
            direction=lc.direction,
            observed_delta=lc.observed_delta,
        )
        for lc in scored
    ]
