"""EDIS L4 — Decision Engine service (``decision_engine``).

Answers *"what should we do?"*: consume ``edis.findings.v1`` (a computed
:class:`~edis_contracts.findings.Finding`), classify a **playbook intent**, bind a
typed ``ActionTemplate``, retrieve the numeric facts the finding carries, and
compute a **deterministic** :class:`~edis_contracts.decisions.ImpactEstimate` +
:class:`~edis_contracts.decisions.ConfidenceScore` + priority, then persist and
publish a :class:`~edis_contracts.decisions.Recommendation`.

THE NUMBERS RULE (the unbreakable pin of this layer):

* **ALL of impact, confidence, and priority come from deterministic, unit-tested
  code — NEVER from the LLM.** The synthesis/scoring core is pure: it needs no
  database, no broker, and no API key, and produces byte-identical numbers every
  run.
* The LLM is used only for (a) *optional* intent classification (Haiku 4.5
  structured output) with a deterministic rule-based fallback, and (b) *optional*
  narrative prose (Opus, post-validated against ``impact.inputs`` and discarded on
  mismatch). With no ``ANTHROPIC_API_KEY`` the engine works fully via the
  rule-based classifier and emits no prose.

This package is import-safe with **no infrastructure**: importing it (or building
the FastAPI app) connects to no Postgres / Redpanda / Redis and requires no
Anthropic API key.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
