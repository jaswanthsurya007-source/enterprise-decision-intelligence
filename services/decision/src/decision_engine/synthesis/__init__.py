"""Synthesis: intent classification, playbook binding, and the pure synthesize() entry.

The classifier picks only a playbook *label* (Haiku structured output, or the
deterministic rule); the playbook registry binds a typed :class:`ActionTemplate`; and
:func:`synthesize` orchestrates classify -> bind -> retrieve -> score -> Recommendation.
No number on the resulting recommendation comes from the LLM.
"""

from __future__ import annotations

from decision_engine.synthesis.intent_classifier import (
    Classifier,
    IntentClassifier,
    RuleBasedIntentClassifier,
    classify_by_rule,
    make_intent_classifier,
)
from decision_engine.synthesis.playbook_registry import PlaybookRegistry
from decision_engine.synthesis.playbooks.base import (
    ActionTemplate,
    BoundAction,
    PlaybookIntent,
)
from decision_engine.synthesis.synthesizer import synthesize

__all__ = [
    "Classifier",
    "IntentClassifier",
    "RuleBasedIntentClassifier",
    "classify_by_rule",
    "make_intent_classifier",
    "PlaybookRegistry",
    "ActionTemplate",
    "BoundAction",
    "PlaybookIntent",
    "synthesize",
]
