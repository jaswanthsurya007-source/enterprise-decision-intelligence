"""EDIS canonical contracts -- the single source of truth for every service.

A breaking change here fails type-checking across all dependents in CI (intended).
The golden-JSON stability test catches accidental field/type drift.
"""

from __future__ import annotations

from edis_contracts.canonical import (
    CanonicalCustomer,
    CanonicalOrder,
    CanonicalOrderLine,
    CanonicalProduct,
    CustomerActivity,
    MetricObservation,
    OpsEvent,
    SourceRef,
)
from edis_contracts.decisions import (
    ConfidenceScore,
    ImpactEstimate,
    OutcomeReport,
    Recommendation,
    RecommendationLifecycleEvent,
)
from edis_contracts.events import CanonicalEvent, LineageEvent, MetricPoint
from edis_contracts.findings import (
    CandidateCause,
    EvidenceBundle,
    EvidenceItem,
    Finding,
    FindingKind,
    Forecast,
)
from edis_contracts.governance import AuditEvent, Decision, Evidence
from edis_contracts.ingest import (
    CustomerPayloadV1,
    DLQRecord,
    Domain,
    IngestEnvelope,
    OpsPayloadV1,
    QuarantinedRecord,
    SalesPayloadV1,
)
from edis_contracts.security import Actor, ResourceRef, Role, SecurityContext

__version__ = "0.1.0"

__all__ = [
    # ingest
    "IngestEnvelope",
    "SalesPayloadV1",
    "OpsPayloadV1",
    "CustomerPayloadV1",
    "DLQRecord",
    "QuarantinedRecord",
    "Domain",
    # canonical
    "SourceRef",
    "CanonicalCustomer",
    "CanonicalProduct",
    "CanonicalOrder",
    "CanonicalOrderLine",
    "OpsEvent",
    "CustomerActivity",
    "MetricObservation",
    # events
    "MetricPoint",
    "CanonicalEvent",
    "LineageEvent",
    # findings
    "Finding",
    "FindingKind",
    "CandidateCause",
    "EvidenceBundle",
    "EvidenceItem",
    "Forecast",
    # decisions
    "Recommendation",
    "ImpactEstimate",
    "ConfidenceScore",
    "RecommendationLifecycleEvent",
    "OutcomeReport",
    # governance
    "AuditEvent",
    "Decision",
    "Evidence",
    # security
    "SecurityContext",
    "Actor",
    "ResourceRef",
    "Role",
]
