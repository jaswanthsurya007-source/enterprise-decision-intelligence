"""Explainability for L4 recommendations (C2): evidence trail + optional prose.

* :mod:`~decision_engine.explain.evidence_builder` -- assembles the
  ``evidence_trail`` linking the source finding, its metrics, the root-cause candidates,
  and the bound playbook rule, and writes a
  :class:`~edis_contracts.governance.Decision` + :class:`~edis_contracts.governance.Evidence`
  explainability record to the governance service via the
  :class:`~edis_gov_sdk.explain.ExplainabilityClient` (HTTP). The write TOLERATES the
  governance service being absent (CI / the bare app): a failed POST is logged and
  swallowed, never raised into the decision flow.
* :mod:`~decision_engine.explain.narrator` -- the OPTIONAL ``claude-opus-4-8`` prose
  narrator, mirroring the L3 lazy + key-guarded + degrade-to-template pattern. The prose
  is POST-VALIDATED against ``impact.inputs`` (every number it states must be a computed
  fact) and DISCARDED on any mismatch. With no API key there is simply no prose. The
  prose is never numeric authority.
"""

from __future__ import annotations

from decision_engine.explain.evidence_builder import (
    EvidenceBuilder,
    build_decision_record,
    build_evidence_trail,
)
from decision_engine.explain.narrator import (
    RecommendationNarrator,
    allowed_numbers_for,
    make_recommendation_narrator,
)

__all__ = [
    "EvidenceBuilder",
    "build_decision_record",
    "build_evidence_trail",
    "RecommendationNarrator",
    "allowed_numbers_for",
    "make_recommendation_narrator",
]
