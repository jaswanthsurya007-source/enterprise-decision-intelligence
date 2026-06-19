"""Unit tests for the deterministic :class:`ImpactEstimator` (THE NUMBERS RULE).

Asserts the closed-form ``recovery_flat`` math is exact, the +/- band is correct, the
auditable ``inputs`` carry the exact figures used, and the stub ``none`` method never
fabricates a value. No infra, no key -- pure functions over RetrievedFacts.
"""

from __future__ import annotations


from decision_engine.scoring.fact_retriever import FactRetriever, RetrievedFacts
from decision_engine.scoring.impact_estimator import DEFAULT_BAND_FRAC, ImpactEstimator
from decision_engine.synthesis.playbook_registry import PlaybookRegistry
from decision_engine.synthesis.playbooks.base import PlaybookIntent

from edis_l4_testkit import DEMO_NOW, build_demo_finding


def _facts(daily_loss: float, days: float) -> RetrievedFacts:
    return RetrievedFacts(
        observed_value=0.0,
        expected_value=0.0,
        deviation=-daily_loss,
        deviation_abs=daily_loss,
        daily_loss=daily_loss,
        affected_days_total=days,
        affected_days_remaining=days,
        reach=0.5,
    )


def test_recovery_flat_exact_math_and_band():
    """value = daily_loss * days; band = value * (1 -/+ band_frac), exact to the cent."""

    est = ImpactEstimator(band_frac=0.30)
    action = PlaybookRegistry().resolve(PlaybookIntent.OPERATIONAL_FIX, build_demo_finding())

    impact = est.estimate(action, _facts(34000.0, 5.0))

    assert impact.method == "recovery_flat"
    assert impact.value == 34000.0 * 5.0 == 170000.0
    assert impact.value_low == round(170000.0 * 0.70, 2) == 119000.0
    assert impact.value_high == round(170000.0 * 1.30, 2) == 221000.0
    assert impact.direction == "increase"  # mitigating the regression recovers revenue
    assert impact.unit == "USD"
    assert impact.horizon_days == 5


def test_recovery_flat_inputs_are_exact_and_auditable():
    """The inputs dict records the EXACT figures used (the grounding/audit provenance)."""

    est = ImpactEstimator()
    action = PlaybookRegistry().resolve(PlaybookIntent.OPERATIONAL_FIX, build_demo_finding())

    impact = est.estimate(action, _facts(34000.0, 5.0))

    assert impact.inputs == {"daily_loss": 34000.0, "affected_days_remaining": 5.0}
    # value is reconstructible from the auditable inputs alone (no hidden numbers).
    assert impact.value == impact.inputs["daily_loss"] * impact.inputs["affected_days_remaining"]


def test_recovery_flat_demo_value_in_required_band():
    """End-to-end over the demo finding + facts: impact.value lands in $120K-$200K."""

    retriever = FactRetriever()
    facts = retriever.retrieve(build_demo_finding(), now=DEMO_NOW)
    action = PlaybookRegistry().resolve(PlaybookIntent.OPERATIONAL_FIX, build_demo_finding())

    impact = ImpactEstimator().estimate(action, facts)

    assert impact.value == 170000.0
    assert 120000.0 <= impact.value <= 200000.0


def test_estimator_is_pure_and_deterministic():
    """Identical (action, facts) always yields an identical estimate."""

    est = ImpactEstimator()
    action = PlaybookRegistry().resolve(PlaybookIntent.OPERATIONAL_FIX, build_demo_finding())
    facts = _facts(12345.0, 3.0)

    a = est.estimate(action, facts)
    b = est.estimate(action, facts)

    assert a.model_dump() == b.model_dump()
    assert a.value == 12345.0 * 3.0


def test_custom_band_fraction_widens_symmetrically():
    """A different band_frac changes only the band, symmetrically around value."""

    est = ImpactEstimator(band_frac=0.10)
    action = PlaybookRegistry().resolve(PlaybookIntent.OPERATIONAL_FIX, build_demo_finding())

    impact = est.estimate(action, _facts(100000.0, 1.0))

    assert impact.value == 100000.0
    assert impact.value_low == 90000.0
    assert impact.value_high == 110000.0


def test_default_band_frac_constant_is_30pct():
    assert DEFAULT_BAND_FRAC == 0.30


def test_stub_playbook_never_invents_a_value():
    """An un-built (stub) playbook -> the ``none`` sentinel: zero value, empty inputs."""

    est = ImpactEstimator()
    # ``investigate`` is a typed stub (built=False, impact_method="none").
    action = PlaybookRegistry().resolve(PlaybookIntent.INVESTIGATE, build_demo_finding())

    impact = est.estimate(action, _facts(34000.0, 5.0))

    assert impact.method == "none"
    assert impact.value == 0.0
    assert impact.value_low == 0.0
    assert impact.value_high == 0.0
    assert impact.inputs == {}
