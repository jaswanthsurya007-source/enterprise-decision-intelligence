"""EDIS L1 — Data Ingestion (``apps/ingestion``).

The **edge of trust**: the only component permitted to accept untrusted source
data. It guarantees *structural validity, source fidelity, and at-least-once
delivery with idempotency* — but not semantic correctness across sources (that is
L2's job).

The single public entry point for processing one record is
:func:`ingestion.pipeline.engine.ingest_record`; I2 (REST + control API) and I3
(simulator + batch loader + CLI) both reuse that one code path so the real-time
and batch ingress modes never drift.
"""

from __future__ import annotations

__version__ = "0.1.0"
