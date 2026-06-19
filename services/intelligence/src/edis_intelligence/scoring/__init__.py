"""Severity / confidence / business-impact-input normalization (L3 scoring).

Pure functions that map a :class:`~edis_intelligence.detectors.base.DetectorResult`
to the normalized 0..1 ``severity`` / ``confidence`` / ``business_impact_input``
fields of a :class:`edis_contracts.findings.Finding`. L3 supplies the *input*; L4
owns the final business ranking.
"""

from __future__ import annotations

from edis_intelligence.scoring.normalize import (
    ScoredResult,
    business_impact_input,
    confidence,
    score_result,
    severity,
)

__all__ = [
    "ScoredResult",
    "severity",
    "confidence",
    "business_impact_input",
    "score_result",
]
