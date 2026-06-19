"""Map detector output to normalized severity / confidence / impact-input.

All three are pure, deterministic functions of a
:class:`~edis_intelligence.detectors.base.DetectorResult` (+ its
:class:`~edis_intelligence.detectors.base.DetectionContext`), bounded to ``[0, 1]``.
No I/O, no LLM. L3 emits these as the Finding's ``severity`` / ``confidence`` /
``business_impact_input``; L4 owns the final business ranking.

* **severity** — *how anomalous* the deviation is. A smooth saturating map of the
  detector-native ``score`` (robust z / STL residual-sigmas): a score at the
  detector threshold maps to a moderate severity and grows toward 1 as the score
  climbs, so a 5.8σ revenue drop reads as high severity without ever hitting a
  hard ceiling artifact.
* **confidence** — *how much we trust the detection*. Combines (a) how far the
  score clears the detection threshold and (b) how much baseline history backed
  the call (more samples -> more confidence). Independent of business stakes.
* **business_impact_input** — *magnitude × direction × reach* in 0..1. Magnitude
  is the relative deviation; direction is whether the move is the *adverse* one
  for this metric (a revenue drop or an error-rate spike counts fully; a
  favorable move is discounted); reach scales with the number of dimensions
  pinned (a tightly-scoped cell is more localized/actionable). L4 turns this into
  currency.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from edis_intelligence.detectors.base import DetectionContext, DetectorResult

#: Score (z / residual-sigma) at which severity reaches the saturation midpoint.
_SEVERITY_MIDPOINT = 4.0
#: Logistic steepness for the severity map.
_SEVERITY_STEEPNESS = 0.6
#: Baseline-sample count at which the "history" confidence term saturates.
_CONFIDENCE_FULL_HISTORY = 28.0


def _clip01(x: float) -> float:
    """Clamp ``x`` into the closed unit interval."""

    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return float(x)


def severity(result: DetectorResult) -> float:
    """Normalized 0..1 severity from the detector-native ``score`` (magnitude).

    A logistic in ``|score|`` centered at :data:`_SEVERITY_MIDPOINT`. Monotone
    increasing, asymptotic to 1, so larger anomalies always score higher without a
    flat ceiling.
    """

    z = abs(result.score)
    raw = 1.0 / (1.0 + math.exp(-_SEVERITY_STEEPNESS * (z - _SEVERITY_MIDPOINT)))
    return _clip01(raw)


def confidence(result: DetectorResult, ctx: DetectionContext | None = None) -> float:
    """Normalized 0..1 confidence in the detection.

    Blends two terms:

    * **margin** — how far ``|score|`` clears the detection threshold (relative to
      the threshold), saturating; a call right at the threshold is low-confidence,
      one well past it is high-confidence.
    * **history** — how much baseline supported the call, read from the detector's
      diagnostics (``baseline_n``) and saturating at
      :data:`_CONFIDENCE_FULL_HISTORY` samples.

    The two are combined multiplicatively-ish via a weighted blend so a detection
    needs *both* a clear margin and enough history to reach high confidence.
    """

    threshold = _threshold_for(result, ctx)
    z = abs(result.score)
    # Margin term: 0 at the threshold, -> 1 as the score doubles the threshold.
    margin = _clip01((z - threshold) / threshold) if threshold > 0 else _clip01(z)
    margin = 0.5 + 0.5 * margin  # a flag at the threshold still earns ~0.5

    baseline_n = (
        result.diagnostics.get("baseline_n") or result.diagnostics.get("eval_window") or 0.0
    )
    history = _clip01(baseline_n / _CONFIDENCE_FULL_HISTORY)
    # Weighted blend: margin dominates, history modulates.
    return _clip01(0.65 * margin + 0.35 * history)


def business_impact_input(result: DetectorResult, ctx: DetectionContext | None = None) -> float:
    """Normalized 0..1 business-impact *input* = magnitude × direction × reach.

    * **magnitude** — relative deviation ``|deviation_pct| / 100`` saturated at 1
      (a >=100% move is "full" magnitude).
    * **direction** — 1.0 when the move is the *adverse* direction for the metric
      (``ctx.direction``: a ``revenue`` drop, an ``error_rate``/``latency_p95``
      rise), 0.4 when it is favorable, 1.0 when ``direction == "both"`` / unknown.
    * **reach** — grows with the number of dimensions pinned: a fully-unscoped
      (tenant-wide) anomaly reaches everything (1.0); each pinned dimension
      narrows it. Modeled as ``1 / (1 + k)`` blended toward a floor so a localized
      cell still carries meaningful impact.

    L4 converts this dimensionless 0..1 into a currency estimate; here it is just a
    comparable prioritization input.
    """

    magnitude = _clip01(abs(result.deviation_pct) / 100.0)

    direction_mult = 1.0
    if ctx is not None and ctx.direction in {"up", "down"}:
        adverse = (ctx.direction == "down" and result.deviation < 0) or (
            ctx.direction == "up" and result.deviation > 0
        )
        direction_mult = 1.0 if adverse else 0.4

    k = len(result.dimensions)
    # reach in [0.5, 1.0]: unscoped -> 1.0; more dims -> smaller but floored.
    reach = 0.5 + 0.5 * (1.0 / (1.0 + 0.5 * k))

    return _clip01(magnitude * direction_mult * reach)


def _threshold_for(result: DetectorResult, ctx: DetectionContext | None) -> float:
    """Best-effort recovery of the detection threshold used (for the margin term)."""

    if ctx is not None:
        if result.detector == "robust_zscore" and ctx.z_threshold is not None:
            return float(ctx.z_threshold)
        if result.detector == "stl_seasonal" and ctx.level_shift_k is not None:
            return float(ctx.level_shift_k)
    # Sensible defaults matching the detector constructors.
    if result.detector == "robust_zscore":
        return 3.5
    if result.detector == "stl_seasonal":
        return 3.5
    return 3.0


@dataclass(frozen=True)
class ScoredResult:
    """A :class:`DetectorResult` with its normalized scores filled in."""

    result: DetectorResult


def score_result(result: DetectorResult, ctx: DetectionContext | None = None) -> DetectorResult:
    """Return a copy of ``result`` with ``severity``/``confidence``/``business_impact_input`` set.

    Pure: produces a new frozen :class:`DetectorResult`; the detector core stays
    scoring-free.
    """

    return replace(
        result,
        severity=severity(result),
        confidence=confidence(result, ctx),
        business_impact_input=business_impact_input(result, ctx),
    )
