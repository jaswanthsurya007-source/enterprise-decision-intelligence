"""Deterministic ConfidenceScore: blend finding confidence + evidence + static prior.

The :class:`ConfidenceScorer` produces a :class:`~edis_contracts.decisions.ConfidenceScore`
whose ``value`` is a fixed weighted blend of three components -- all computed, never
from the LLM:

* ``insight`` -- the finding's own ``confidence`` (how sure L3 is the anomaly is real).
* ``evidence`` -- an **evidence-completeness** score derived from how much corroborating
  signal the finding carries (candidate causes present, contribution attributed, a
  narrative/evidence ref attached). Pure function of the finding's structure.
* ``historical_calibration`` -- a **static per-(tenant, playbook) calibration prior**
  looked up from a provider (the DB-backed table in C2, an in-memory map in tests, or a
  flat default). ``calibration_n`` is always 0 in the MVP (no live feedback loop).

``value = (w_i*insight + w_e*evidence + w_c*calibration) / (w_i + w_e + w_c)``, clamped
to [0, 1]. On the demo finding (``insight ~= 0.91``, strong evidence, prior ~0.74) this
lands in the required ~0.8-0.9 band. Deterministic given (finding, prior). No infra/key.
"""

from __future__ import annotations

from typing import Protocol

from edis_contracts.decisions import ConfidenceScore
from edis_contracts.findings import Finding

#: Default static calibration prior (mirrors DecisionSettings.default_calibration_prior).
DEFAULT_PRIOR = 0.74
#: Default blend weights (mirror DecisionSettings.confidence_weight_*).
DEFAULT_W_INSIGHT = 0.45
DEFAULT_W_EVIDENCE = 0.30
DEFAULT_W_CALIBRATION = 0.25


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def evidence_completeness(finding: Finding) -> float:
    """Score how complete the corroborating evidence is (0..1), purely structurally.

    Built from cheap, deterministic structural signals on the finding:

    * a base from the finding's own ``business_impact_input`` (L3's reach proxy);
    * a bonus for having ranked candidate causes (RCA found drivers);
    * a bonus when those causes carry attributed ``contribution_pct``;
    * a small bonus when an evidence bundle / narrative is attached.

    Clamped to [0, 1]. No averaging over time, no LLM -- just "how much computed
    backing does this finding carry".
    """

    score = 0.55 * _clamp01(float(finding.business_impact_input))
    causes = finding.candidate_causes
    if causes:
        score += 0.25
        if any(c.contribution_pct is not None for c in causes):
            score += 0.12
    if finding.evidence_ref is not None or finding.narrative is not None:
        score += 0.08
    return _clamp01(score)


class CalibrationPriorProvider(Protocol):
    """Port: return the static historical_calibration prior for (tenant, playbook)."""

    def prior_for(self, tenant_id: str, playbook_id: str) -> tuple[float, int]:
        """Return ``(prior_value, calibration_n)``. ``calibration_n`` is 0 in the MVP."""
        ...


class StaticCalibrationPriorProvider:
    """In-memory / default :class:`CalibrationPriorProvider` (no DB).

    Looks up a per-(tenant, playbook) prior from an optional map; falls back to a flat
    default when absent. Always reports ``calibration_n=0`` -- the MVP has no live loop,
    so the prior is pre-seeded, not learned. The DB-backed provider (C2) implements the
    same port over the ``calibration_prior`` table.
    """

    def __init__(
        self,
        *,
        default_prior: float = DEFAULT_PRIOR,
        overrides: dict[tuple[str, str], float] | None = None,
    ) -> None:
        self._default = _clamp01(float(default_prior))
        self._overrides = dict(overrides or {})

    def prior_for(self, tenant_id: str, playbook_id: str) -> tuple[float, int]:
        prior = self._overrides.get((tenant_id, playbook_id), self._default)
        return _clamp01(float(prior)), 0


class ConfidenceScorer:
    """Blends insight + evidence + a static calibration prior into a ConfidenceScore."""

    def __init__(
        self,
        prior_provider: CalibrationPriorProvider | None = None,
        *,
        w_insight: float = DEFAULT_W_INSIGHT,
        w_evidence: float = DEFAULT_W_EVIDENCE,
        w_calibration: float = DEFAULT_W_CALIBRATION,
    ) -> None:
        self._priors = prior_provider or StaticCalibrationPriorProvider()
        self._wi = float(w_insight)
        self._we = float(w_evidence)
        self._wc = float(w_calibration)

    def score(self, finding: Finding, *, playbook_id: str) -> ConfidenceScore:
        """Return the deterministic :class:`ConfidenceScore` for this finding+playbook."""

        insight = _clamp01(float(finding.confidence))
        evidence = evidence_completeness(finding)
        calibration, calibration_n = self._priors.prior_for(finding.tenant_id, playbook_id)

        weight_sum = self._wi + self._we + self._wc
        blended = (
            (self._wi * insight + self._we * evidence + self._wc * calibration) / weight_sum
            if weight_sum > 0
            else 0.0
        )

        return ConfidenceScore(
            value=round(_clamp01(blended), 4),
            components={
                "insight": round(insight, 4),
                "evidence": round(evidence, 4),
                "historical_calibration": round(calibration, 4),
            },
            calibration_n=calibration_n,
        )
