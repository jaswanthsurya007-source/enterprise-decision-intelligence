"""Deterministically pull the numeric inputs the estimators need from a Finding.

The :class:`FactRetriever` is the boundary between *computed facts* and the
*deterministic estimators*. In the MVP every fact it needs is already present on the
:class:`~edis_contracts.findings.Finding` the engine consumes (observed / expected /
deviation, the analysis window, and reach in the dimensions), so the retriever reads
them straight off the finding -- no DB / Timescale / Redis call required. The DB-backed
enrichment (e.g. cross-checking current values) is a future seam; the contract here is
the :class:`RetrievedFacts` shape the estimators consume.

CRITICAL: the retriever only *reads* and *derives* numbers from the finding's own
computed fields. It never invents a value and never calls the LLM. ``affected_days_remaining``
is derived from the finding's window vs. ``now`` (clamped to >= 1 day so a same-day
incident still prices a full day of recovery), and ``daily_loss`` is the absolute
per-day deviation. Pure and deterministic given (finding, now).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from edis_contracts.findings import Finding


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RetrievedFacts:
    """The numeric inputs the deterministic estimators price against.

    Every field is a computed/derived figure with provenance back to the finding.
    ``extra`` carries any method-specific auxiliary inputs without widening the core
    shape. These are the values that land verbatim in ``ImpactEstimate.inputs``.
    """

    observed_value: float
    expected_value: float
    deviation: float  # observed - expected (signed)
    deviation_abs: float  # |deviation| -- the per-day magnitude
    daily_loss: float  # absolute per-day shortfall (== deviation_abs for daily series)
    affected_days_total: float  # full span of the incident window, in days (>= 1)
    affected_days_remaining: float  # days from now to window_end (>= 1; future-recovery horizon)
    reach: float  # 0..1 normalized reach proxy (severity input from L3)
    extra: dict[str, float] = field(default_factory=dict)


def _window_days(finding: Finding) -> float:
    """Whole-day span of the finding's analysis window (>= 1.0)."""

    span = (finding.window_end - finding.window_start).total_seconds() / 86400.0
    return max(1.0, math.ceil(span))


def _days_remaining(finding: Finding, now: datetime) -> float:
    """Days from ``now`` to the window end (>= 1.0).

    Models "if we fix this now, how many more days of loss do we avoid". Clamped to a
    minimum of one whole day so a brand-new same-day incident still prices a full day
    of recovery (the demo finding's window ends ~today, so this yields the seeded
    affected-days value rather than zero).
    """

    remaining = (finding.window_end - now).total_seconds() / 86400.0
    return max(1.0, math.ceil(remaining))


class FactRetriever:
    """Reads numeric inputs off a finding (and, in future, the data tier)."""

    def retrieve(self, finding: Finding, *, now: datetime | None = None) -> RetrievedFacts:
        """Build :class:`RetrievedFacts` from ``finding`` (pure given ``now``)."""

        now = now or _utc_now()
        deviation = float(finding.deviation)
        deviation_abs = abs(deviation)
        return RetrievedFacts(
            observed_value=float(finding.observed_value),
            expected_value=float(finding.expected_value),
            deviation=deviation,
            deviation_abs=deviation_abs,
            daily_loss=deviation_abs,
            affected_days_total=_window_days(finding),
            affected_days_remaining=_days_remaining(finding, now),
            reach=float(finding.severity),
            extra={
                "deviation_pct": float(finding.deviation_pct),
                "score": float(finding.score),
            },
        )
