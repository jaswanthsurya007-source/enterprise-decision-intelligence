"""EDIS L3 — Intelligence Engine service (``edis_intelligence``).

Turns clean canonical metrics into *computed, grounded facts about what changed,
why, and what's next*: classical anomaly detection (robust z-score + STL
level-shift), lag-aware root-cause ranking, a single ETS forecast band, and a
grounded LLM narrative that the grounding guard validates against the evidence.

Two unbreakable rules of this layer:

* **The LLM never invents numbers.** Every figure on a ``Finding`` is computed by
  a detector; the narrator only reasons over the ``EvidenceBundle``.
* **Detection never depends on the LLM.** The detection + scoring core is pure,
  deterministic, and unit-testable with in-memory series — no DB, no broker, no
  API keys.

This package is import-safe with **no infrastructure**: importing it (or building
the FastAPI app) connects to no Postgres / Redpanda / Redis and requires no
Anthropic / Voyage API key.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
