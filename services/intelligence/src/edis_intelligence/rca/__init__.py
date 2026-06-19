"""L3 root-cause analysis — pure, deterministic, infrastructure-free.

This package is the analysis core of the Intelligence Engine's RCA: given a target
anomalous metric series (e.g. EMEA-web ``revenue`` flagged as a ``LEVEL_SHIFT``) and
a set of candidate driver series (e.g. EMEA ``checkout-api`` ``latency_p95`` /
``error_rate``), it

* :mod:`~edis_intelligence.rca.correlation` — ranks candidate drivers by *lag-aware*
  cross-correlation, returning :class:`edis_contracts.findings.CandidateCause`
  objects (correlation, lag, direction, observed delta);
* :mod:`~edis_intelligence.rca.decomposition` — attributes *which dimension*
  (region / channel / service) drove a change, and turns relative explanatory
  weight into ``contribution_pct``;
* :mod:`~edis_intelligence.rca.evidence` — assembles the
  :class:`edis_contracts.findings.EvidenceBundle`: the computed
  :class:`~edis_contracts.findings.EvidenceItem` facts the narrator may cite, plus
  the ``allowed_numbers`` whitelist the grounding guard enforces.

Everything here imports only numpy / pandas / the contracts library: no DB, no
broker, no LLM, no API keys. The functions are deterministic so the demo math is
directly unit-testable.
"""

from __future__ import annotations

from edis_intelligence.rca.correlation import (
    LaggedCorrelation,
    cross_correlate,
    rank_candidate_causes,
)
from edis_intelligence.rca.decomposition import (
    DimensionContribution,
    contribution_pct_from_causes,
    dimensional_contributions,
)
from edis_intelligence.rca.evidence import build_evidence_bundle

__all__ = [
    "LaggedCorrelation",
    "cross_correlate",
    "rank_candidate_causes",
    "DimensionContribution",
    "dimensional_contributions",
    "contribution_pct_from_causes",
    "build_evidence_bundle",
]
