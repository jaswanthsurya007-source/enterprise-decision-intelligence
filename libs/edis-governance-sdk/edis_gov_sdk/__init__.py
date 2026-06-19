"""EDIS governance SDK -- thin emitters every layer imports to stay governed.

Three concerns, three seams:

* :class:`AuditEmitter` / :func:`emit_audit` -- publish :class:`AuditEvent`\\ s to
  ``edis.governance.audit.v1`` (the append-only audit spine).
* :class:`LineageEmitter` / :func:`emit_lineage` -- publish :class:`LineageEvent`\\ s
  to ``edis.governance.lineage.v1`` (input/output edges per processing run).
* :class:`ExplainabilityClient` -- POST :class:`Decision` records to the
  governance service over HTTP (the SDK owns no DB).

Audit and lineage publish through an injected
:class:`~edis_platform.bus.base.EventSink`, so they work over Kafka, Redis
Streams, or the in-process bus identically. Nothing here connects to a broker or
HTTP service at import time.
"""

from __future__ import annotations

from edis_gov_sdk.audit import AuditEmitter, build_audit_event, emit_audit
from edis_gov_sdk.explain import ExplainabilityClient
from edis_gov_sdk.lineage import LineageEmitter, build_lineage_event, emit_lineage

__version__ = "0.1.0"

__all__ = [
    # audit
    "AuditEmitter",
    "emit_audit",
    "build_audit_event",
    # lineage
    "LineageEmitter",
    "emit_lineage",
    "build_lineage_event",
    # explainability
    "ExplainabilityClient",
]
