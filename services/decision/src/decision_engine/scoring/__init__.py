"""Deterministic scoring core: fact retrieval, impact, confidence, prioritization.

THE NUMBERS RULE: every figure on a recommendation (impact, confidence, priority)
is produced by the pure, unit-testable functions in this package -- never by the LLM.
All four collaborators are infra-free and deterministic given their inputs.
"""

from __future__ import annotations

from decision_engine.scoring.confidence_scorer import (
    ConfidenceScorer,
    StaticCalibrationPriorProvider,
    evidence_completeness,
)
from decision_engine.scoring.fact_retriever import FactRetriever, RetrievedFacts
from decision_engine.scoring.impact_estimator import ImpactEstimator
from decision_engine.scoring.prioritizer import PriorityInputs, Prioritizer, effort_units

__all__ = [
    "FactRetriever",
    "RetrievedFacts",
    "ImpactEstimator",
    "ConfidenceScorer",
    "StaticCalibrationPriorProvider",
    "evidence_completeness",
    "Prioritizer",
    "PriorityInputs",
    "effort_units",
]
