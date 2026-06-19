"""Unit tests for the deterministic :class:`ConfidenceScorer`.

Asserts: the blend is the documented weighted average, bounded to [0, 1]; the static
per-(tenant, playbook) prior is applied (default + override); ``calibration_n`` is always 0
in the MVP; the components dict carries insight / evidence / historical_calibration; and
the demo finding lands confidence in 0.8-0.9. No infra, no key.
"""

from __future__ import annotations

from decision_engine.scoring.confidence_scorer import (
    DEFAULT_PRIOR,
    DEFAULT_W_CALIBRATION,
    DEFAULT_W_EVIDENCE,
    DEFAULT_W_INSIGHT,
    ConfidenceScorer,
    StaticCalibrationPriorProvider,
    evidence_completeness,
)

from edis_l4_testkit import DEMO_TENANT, build_demo_finding


def test_blend_is_weighted_average_of_three_components():
    """value == (wi*insight + we*evidence + wc*calibration) / (wi+we+wc)."""

    finding = build_demo_finding()
    scorer = ConfidenceScorer(StaticCalibrationPriorProvider(default_prior=0.74))

    score = scorer.score(finding, playbook_id="operational_fix")

    insight = score.components["insight"]
    evidence = score.components["evidence"]
    calibration = score.components["historical_calibration"]
    expected = (
        DEFAULT_W_INSIGHT * insight
        + DEFAULT_W_EVIDENCE * evidence
        + DEFAULT_W_CALIBRATION * calibration
    ) / (DEFAULT_W_INSIGHT + DEFAULT_W_EVIDENCE + DEFAULT_W_CALIBRATION)

    assert score.value == round(expected, 4)


def test_components_are_insight_evidence_calibration():
    score = ConfidenceScorer().score(build_demo_finding(), playbook_id="operational_fix")

    assert set(score.components) == {"insight", "evidence", "historical_calibration"}
    # insight is the finding's own confidence.
    assert score.components["insight"] == round(build_demo_finding().confidence, 4)


def test_value_is_bounded_0_1():
    """Even with degenerate weights/priors the blended value stays within [0, 1]."""

    finding = build_demo_finding()

    # All-max inputs.
    hi = ConfidenceScorer(StaticCalibrationPriorProvider(default_prior=1.0)).score(
        finding.model_copy(update={"confidence": 1.0, "business_impact_input": 1.0}),
        playbook_id="p",
    )
    assert 0.0 <= hi.value <= 1.0

    # All-min inputs.
    lo = ConfidenceScorer(StaticCalibrationPriorProvider(default_prior=0.0)).score(
        finding.model_copy(
            update={
                "confidence": 0.0,
                "business_impact_input": 0.0,
                "candidate_causes": [],
                "evidence_ref": None,
                "narrative": None,
            }
        ),
        playbook_id="p",
    )
    assert 0.0 <= lo.value <= 1.0
    assert lo.value == 0.0


def test_static_prior_default_is_applied():
    score = ConfidenceScorer(StaticCalibrationPriorProvider(default_prior=0.74)).score(
        build_demo_finding(), playbook_id="operational_fix"
    )

    assert score.components["historical_calibration"] == 0.74
    assert DEFAULT_PRIOR == 0.74


def test_static_prior_override_per_tenant_playbook():
    """An override map keyed by (tenant, playbook) replaces the flat default."""

    provider = StaticCalibrationPriorProvider(
        default_prior=0.50,
        overrides={(DEMO_TENANT, "operational_fix"): 0.90},
    )

    matched = provider.prior_for(DEMO_TENANT, "operational_fix")
    unmatched = provider.prior_for(DEMO_TENANT, "pricing_change")

    assert matched == (0.90, 0)
    assert unmatched == (0.50, 0)


def test_calibration_n_is_zero_in_mvp():
    """No live feedback loop -> calibration_n is always 0 (static prior)."""

    score = ConfidenceScorer().score(build_demo_finding(), playbook_id="operational_fix")
    assert score.calibration_n == 0

    _prior, n = StaticCalibrationPriorProvider().prior_for("any", "any")
    assert n == 0


def test_demo_confidence_in_required_band():
    """The §9 demo finding -> confidence in the required 0.8-0.9 band (~0.84)."""

    score = ConfidenceScorer().score(build_demo_finding(), playbook_id="operational_fix")

    assert 0.8 <= score.value <= 0.9


def test_evidence_completeness_rewards_corroboration():
    """More corroborating structure (causes, contribution, evidence ref) -> higher score."""

    bare = build_demo_finding().model_copy(
        update={"candidate_causes": [], "evidence_ref": None, "narrative": None}
    )
    rich = build_demo_finding()

    assert 0.0 <= evidence_completeness(bare) <= 1.0
    assert 0.0 <= evidence_completeness(rich) <= 1.0
    assert evidence_completeness(rich) > evidence_completeness(bare)


def test_scorer_is_deterministic():
    finding = build_demo_finding()
    a = ConfidenceScorer().score(finding, playbook_id="operational_fix")
    b = ConfidenceScorer().score(finding, playbook_id="operational_fix")
    assert a.model_dump() == b.model_dump()
