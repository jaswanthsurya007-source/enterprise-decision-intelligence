"""Source connectors — the uniform ``async iter_records()`` ingress surface.

A :class:`~ingestion.connectors.base.SourceConnector` yields
:class:`~ingestion.connectors.base.SourceRecord`\\ s (a ``(domain, raw, …)``
tuple) which the pipeline core (:func:`ingestion.pipeline.engine.ingest_record`)
processes identically regardless of where they came from. The deterministic
simulator implements this protocol via the thin
:class:`~ingestion.connectors.sales.SalesSimConnector` /
:class:`~ingestion.connectors.ops.OpsSimConnector` adapters (and the combined
:class:`~ingestion.connectors.base.SimulatorConnector`); the batch loader feeds
the same core from files. (A customer-activity connector is a stub seam in the
MVP.)
"""

from __future__ import annotations

from ingestion.connectors.base import (
    SimulatorConnector,
    SourceConnector,
    SourceRecord,
)
from ingestion.connectors.ops import OpsSimConnector
from ingestion.connectors.sales import SalesSimConnector

__all__ = [
    "SourceConnector",
    "SourceRecord",
    "SimulatorConnector",
    "SalesSimConnector",
    "OpsSimConnector",
]
