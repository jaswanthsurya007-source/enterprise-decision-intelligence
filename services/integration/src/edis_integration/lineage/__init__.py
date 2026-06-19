"""Integration-stage data lineage (raw_event -> canonical -> metric edges).

Re-exports :func:`build_integration_lineage` (the single edge-construction
helper, shared by the engine's outbox path) and
:class:`IntegrationLineageEmitter` (the direct sink-publish path for tooling).
Built on :class:`edis_gov_sdk.lineage.LineageEmitter`; publishes to
``edis.governance.lineage.v1``.
"""

from __future__ import annotations

from edis_integration.lineage.emitter import (
    IntegrationLineageEmitter,
    build_integration_lineage,
)

__all__ = ["IntegrationLineageEmitter", "build_integration_lineage"]
