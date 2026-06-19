"""Deterministic prioritization: priority_score = (impact * confidence) / effort_units.

The :class:`Prioritizer` turns an :class:`~edis_contracts.decisions.ImpactEstimate` and a
:class:`~edis_contracts.decisions.ConfidenceScore` into a single ``priority_score``, and
assigns ``priority_rank`` across a set of candidate recommendations. All deterministic --
no LLM ever touches the ranking.

* ``effort_units`` is a fixed monotone map from the qualitative ``effort_tier``
  (xs < s < m < l < xl), plus a small floor so the division is always finite.
* ``priority_score = (impact.value * confidence.value) / effort_units``, then normalized
  toward ~0..1 against a believable anchor (``priority_norm_anchor``) so a single
  high-value recommendation reads as ~0.9+ (the demo's rank-1 card). The raw,
  un-normalized score is monotone in value*confidence/effort, so RANKING is invariant to
  the normalization -- normalization only makes the displayed number readable.
* ``priority_rank`` is assigned 1..N over a list, highest score first (ties broken
  deterministically by recommendation id), so a single recommendation is always rank 1.

Pure given the inputs; safe to unit-test with no infra.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from edis_contracts.decisions import ConfidenceScore, ImpactEstimate

#: effort_tier literals (mirrors the Recommendation contract; kept local so the pure
#: scoring core never imports the synthesis package -- avoids an import cycle).
EffortTier = Literal["xs", "s", "m", "l", "xl"]

#: Monotone effort-tier -> effort_units map (smaller = cheaper => higher priority).
EFFORT_UNITS: dict[EffortTier, float] = {
    "xs": 1.0,
    "s": 2.0,
    "m": 4.0,
    "l": 8.0,
    "xl": 16.0,
}

#: Defaults (mirror DecisionSettings).
DEFAULT_EFFORT_FLOOR = 0.5
DEFAULT_NORM_ANCHOR = 10000.0


def effort_units(tier: EffortTier, *, floor: float = DEFAULT_EFFORT_FLOOR) -> float:
    """Return the numeric effort for a tier (with a floor so division is finite)."""

    return EFFORT_UNITS.get(tier, EFFORT_UNITS["m"]) + float(floor)


@dataclass(frozen=True)
class PriorityInputs:
    """One candidate's scoring inputs (impact + confidence + effort + a stable id)."""

    recommendation_id: str
    impact: ImpactEstimate
    confidence: ConfidenceScore
    effort_tier: EffortTier


class Prioritizer:
    """Computes ``priority_score`` and assigns ``priority_rank`` deterministically."""

    def __init__(
        self,
        *,
        effort_floor: float = DEFAULT_EFFORT_FLOOR,
        norm_anchor: float = DEFAULT_NORM_ANCHOR,
    ) -> None:
        self._floor = float(effort_floor)
        self._anchor = float(norm_anchor) if norm_anchor > 0 else 1.0

    def raw_score(
        self, impact: ImpactEstimate, confidence: ConfidenceScore, tier: EffortTier
    ) -> float:
        """The un-normalized ``(impact.value * confidence.value) / effort_units``."""

        units = effort_units(tier, floor=self._floor)
        return (float(impact.value) * float(confidence.value)) / units

    def priority_score(
        self, impact: ImpactEstimate, confidence: ConfidenceScore, tier: EffortTier
    ) -> float:
        """Normalized priority in ~0..1 (monotone in the raw score; readable for the UI).

        Normalizes the raw score by ``anchor / effort_units(s)`` -- the raw score a
        single anchor-sized, small-effort recommendation would earn -- and squashes with
        a saturating curve so a strong recommendation reads as ~0.9+ without exceeding 1.
        Ranking is unaffected (the transform is monotone increasing).
        """

        raw = self.raw_score(impact, confidence, tier)
        ref = self._anchor / effort_units("s", floor=self._floor)
        ratio = raw / ref if ref > 0 else 0.0
        # Saturating map: 0 -> 0, ratio=1 -> ~0.5, large -> ->1. Monotone increasing.
        score = ratio / (ratio + 1.0)
        return round(max(0.0, min(1.0, score)), 4)

    def rank(self, candidates: list[PriorityInputs]) -> dict[str, int]:
        """Assign ``priority_rank`` (1=highest) over ``candidates``; return id -> rank.

        Sorted by raw score descending, ties broken by recommendation id ascending so
        the ordering is fully deterministic. A single candidate is always rank 1.
        """

        ordered = sorted(
            candidates,
            key=lambda c: (
                -self.raw_score(c.impact, c.confidence, c.effort_tier),
                c.recommendation_id,
            ),
        )
        return {c.recommendation_id: i + 1 for i, c in enumerate(ordered)}
