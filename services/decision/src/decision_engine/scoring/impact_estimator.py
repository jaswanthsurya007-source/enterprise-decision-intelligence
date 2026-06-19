"""Closed-form, per-template impact estimation -> a deterministic ImpactEstimate.

THE NUMBERS RULE lives here: every figure on the
:class:`~edis_contracts.decisions.ImpactEstimate` is produced by a pure closed-form
function of the :class:`~decision_engine.scoring.fact_retriever.RetrievedFacts` -- never
by the LLM. Each playbook declares an ``impact_method`` name; the estimator dispatches
on it:

* ``recovery_flat`` (the built ``operational_fix`` playbook) =>
  ``value = daily_loss * affected_days_remaining``, with a symmetric low/high band
  (``+/- impact_band_frac``) and the exact auditable ``inputs`` used. On the demo EMEA
  finding (``daily_loss ~= 34000``, ``affected_days_remaining ~= 5``) this lands at
  ``~$170K`` inside the required $120K-$200K band -- and the ``inputs`` dict records
  ``{"daily_loss": 34000.0, "affected_days_remaining": 5.0}`` exactly, so the optional
  prose narrator can be post-validated against it.
* ``none`` (the typed stubs) => a zero, sentinel estimate so an unbuilt playbook
  never fabricates a value.

Pure and deterministic given the facts. No infra, no key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from edis_contracts.decisions import ImpactEstimate

from decision_engine.scoring.fact_retriever import RetrievedFacts

if TYPE_CHECKING:
    # Typed at check time only; importing the synthesis package here would create an
    # import cycle (synthesis.synthesizer imports the scoring core). The estimator only
    # reads ``impact_method`` / ``impact_direction`` off the action (duck-typed).
    from decision_engine.synthesis.playbooks.base import BoundAction

#: Default fractional half-width of the low/high band (mirrors DecisionSettings).
DEFAULT_BAND_FRAC = 0.30


def _recovery_flat(
    facts: RetrievedFacts,
    direction: Literal["increase", "decrease", "mitigate"],
    *,
    band_frac: float,
) -> ImpactEstimate:
    """``operational_fix`` => recoverable value = daily_loss * affected_days_remaining.

    The ``inputs`` dict carries the EXACT figures used (rounded to whole units so the
    contract stays clean and the prose grounding guard matches them), and is the
    auditable provenance the explainability trail and the optional narrator reference.
    """

    daily_loss = round(facts.daily_loss, 2)
    days = round(facts.affected_days_remaining, 2)
    value = round(daily_loss * days, 2)
    low = round(value * (1.0 - band_frac), 2)
    high = round(value * (1.0 + band_frac), 2)
    return ImpactEstimate(
        value=value,
        value_low=low,
        value_high=high,
        unit="USD",
        direction=direction,
        horizon_days=int(round(days)),
        inputs={"daily_loss": daily_loss, "affected_days_remaining": days},
        method="recovery_flat",
    )


def _none(
    facts: RetrievedFacts,
    direction: Literal["increase", "decrease", "mitigate"],
) -> ImpactEstimate:
    """Sentinel zero estimate for un-built (stub) playbooks -- never invents a value."""

    return ImpactEstimate(
        value=0.0,
        value_low=0.0,
        value_high=0.0,
        unit="USD",
        direction=direction,
        horizon_days=int(round(facts.affected_days_remaining)),
        inputs={},
        method="none",
    )


class ImpactEstimator:
    """Dispatches a :class:`BoundAction` to its closed-form impact method."""

    def __init__(self, *, band_frac: float = DEFAULT_BAND_FRAC) -> None:
        self._band_frac = float(band_frac)

    def estimate(self, action: BoundAction, facts: RetrievedFacts) -> ImpactEstimate:
        """Return the deterministic :class:`ImpactEstimate` for ``action``.

        Pure: identical (action, facts) always yields an identical estimate. The
        impact direction comes from the playbook (``operational_fix`` recovers
        revenue => ``increase``), not from any sign the LLM might suggest.
        """

        if action.impact_method == "recovery_flat":
            return _recovery_flat(facts, action.impact_direction, band_frac=self._band_frac)
        # Unknown / "none" -> sentinel zero (stub playbooks).
        return _none(facts, action.impact_direction)
