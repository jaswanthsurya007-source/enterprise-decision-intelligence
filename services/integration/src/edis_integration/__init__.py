"""EDIS L2 — Data Integration service (``edis_integration``).

The system-of-record gatekeeper: *garbage may enter the raw topic; only
canonical, validated, lineage-tagged facts leave.* Deterministic and
reproducible — **no LLM, no fuzzy entity resolution** (deterministic id-keyed
upsert only).

This package is import-safe with **no infrastructure**: importing it (or building
the FastAPI app) connects to no Postgres / Redpanda / Redis. The normalization
pipeline and the metric derivation are pure and directly unit-testable.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
