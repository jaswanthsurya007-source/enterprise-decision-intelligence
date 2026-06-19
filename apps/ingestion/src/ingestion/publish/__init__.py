"""Publish side of the pipeline.

Wraps the platform :func:`edis_platform.bus.base.make_sink` to publish an
:class:`~edis_contracts.ingest.IngestEnvelope` to ``edis.raw.*`` and a
:class:`~edis_contracts.ingest.DLQRecord` to ``edis.dlq.ingest.v1``, and to emit
an :class:`~edis_contracts.governance.AuditEvent` (``DATA_WRITE``) on the *same*
sink via the governance SDK's :class:`~edis_gov_sdk.audit.AuditEmitter`.
"""

from __future__ import annotations

from ingestion.publish.publisher import IngestPublisher, raw_key_for

__all__ = ["IngestPublisher", "raw_key_for"]
