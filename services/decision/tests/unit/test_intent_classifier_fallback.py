"""Unit tests for the rule-based intent classifier fallback (NO key path).

The engine must classify the demo finding to ``operational_fix`` with NO ANTHROPIC_API_KEY:
the composite :class:`IntentClassifier` built with no key holds no LLM and goes straight to
the deterministic rule. Also covers the rule table directly. Pure, async, no infra/key.
"""

from __future__ import annotations

from decision_engine.synthesis.intent_classifier import (
    IntentClassifier,
    RuleBasedIntentClassifier,
    classify_by_rule,
    make_intent_classifier,
)
from decision_engine.synthesis.playbooks.base import PlaybookIntent

from edis_l4_testkit import build_demo_finding


async def test_rule_classifies_demo_to_operational_fix():
    """The §9 EMEA revenue drop with a leading ops cause -> operational_fix."""

    intent = classify_by_rule(build_demo_finding())
    assert intent == PlaybookIntent.OPERATIONAL_FIX


async def test_rule_based_classifier_is_operational_fix_for_demo():
    intent = await RuleBasedIntentClassifier().classify(build_demo_finding())
    assert intent == PlaybookIntent.OPERATIONAL_FIX


async def test_make_classifier_with_no_key_uses_rule_path(no_keys_settings):
    """With no key, make_intent_classifier returns a rule-only composite (no LLM)."""

    classifier = make_intent_classifier(no_keys_settings)
    assert classifier._llm is None  # built lazily only with a key

    result = await classifier.classify_detailed(build_demo_finding())
    assert result.intent == PlaybookIntent.OPERATIONAL_FIX
    assert result.source == "rule"


async def test_make_classifier_use_llm_false_forces_rule(no_keys_settings):
    classifier = make_intent_classifier(no_keys_settings, use_llm=False)
    assert classifier._llm is None
    assert await classifier.classify(build_demo_finding()) == PlaybookIntent.OPERATIONAL_FIX


async def test_composite_with_no_llm_always_uses_rule():
    classifier = IntentClassifier(None)
    result = await classifier.classify_detailed(build_demo_finding())
    assert result.source == "rule"


async def test_revenue_anomaly_without_ops_cause_is_investigate():
    """A revenue drop with NO leading ops cause -> investigate (safe default)."""

    finding = build_demo_finding().model_copy(update={"candidate_causes": []})
    assert classify_by_rule(finding) == PlaybookIntent.INVESTIGATE


async def test_ops_metric_anomaly_is_operational_fix():
    """A first-party ops metric anomaly (error_rate / latency) -> operational_fix."""

    finding = build_demo_finding().model_copy(
        update={"metric_key": "error_rate", "candidate_causes": []}
    )
    assert classify_by_rule(finding) == PlaybookIntent.OPERATIONAL_FIX


async def test_unknown_metric_defaults_to_investigate():
    finding = build_demo_finding().model_copy(
        update={"metric_key": "page_views", "candidate_causes": []}
    )
    assert classify_by_rule(finding) == PlaybookIntent.INVESTIGATE


async def test_classification_is_deterministic():
    finding = build_demo_finding()
    a = classify_by_rule(finding)
    b = classify_by_rule(finding)
    assert a == b == PlaybookIntent.OPERATIONAL_FIX
