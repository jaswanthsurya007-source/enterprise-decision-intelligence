"""The L2 normalization pipeline -- one core, two ingress modes.

Per record: ``decode -> validate-source -> map -> clean -> coerce -> dq_check``,
then the engine upserts and derives metrics. The stages are pure and operate on a
shared :class:`~edis_integration.pipeline.stages.StageContext`; the engine
(``process_envelope``) wires them to a repository + outbox so the whole flow is
unit-testable over the in-proc bus and an in-memory repo, with **no infra**.
"""

from __future__ import annotations

from edis_integration.pipeline.engine import (
    IntegrationOutcome,
    IntegrationResult,
    NormalizationError,
    normalize_envelope,
    process_envelope,
)

__all__ = [
    "IntegrationOutcome",
    "IntegrationResult",
    "NormalizationError",
    "normalize_envelope",
    "process_envelope",
]
