"""The single registry of canonical event-topic names and their payload models.

Topic naming convention: ``edis.<stage>.<name>.v1``. The versioned suffix is
mandatory; evolution is additive; consumers pin a version. ``TOPIC_MODEL`` lets a
generic bus consumer deserialize any topic to its Pydantic model.
"""

from __future__ import annotations

from pydantic import BaseModel

from edis_contracts.decisions import (
    OutcomeReport,
    Recommendation,
    RecommendationLifecycleEvent,
)
from edis_contracts.events import CanonicalEvent, LineageEvent, MetricPoint
from edis_contracts.findings import Finding, Forecast
from edis_contracts.governance import AuditEvent
from edis_contracts.ingest import IngestEnvelope

# --- raw (L1 -> L2) ---
RAW_SALES = "edis.raw.sales.v1"
RAW_OPS = "edis.raw.ops.v1"
RAW_CUSTOMER = "edis.raw.customer.v1"

# --- canonical change feeds (L2 -> L3, gateway) ---
CANONICAL_ORDER = "edis.canonical.order.v1"
CANONICAL_CUSTOMER = "edis.canonical.customer.v1"
CANONICAL_PRODUCT = "edis.canonical.product.v1"

# --- metrics / intelligence ---
METRICS_POINTS = "edis.metrics.points.v1"
FINDINGS = "edis.findings.v1"
FORECASTS = "edis.forecasts.v1"

# --- decisions / feedback ---
RECOMMENDATIONS = "edis.decisions.recommendations.v1"
DECISIONS_LIFECYCLE = "edis.decisions.lifecycle.v1"
FEEDBACK_OUTCOMES = "edis.feedback.outcomes.v1"

# --- governance ---
AUDIT = "edis.governance.audit.v1"
LINEAGE = "edis.governance.lineage.v1"

# --- dead-letter / quarantine ---
DLQ_INGEST = "edis.dlq.ingest.v1"
DLQ_INTEGRATION = "edis.dlq.integration.v1"


def raw_topic(domain: str) -> str:
    return f"edis.raw.{domain}.v1"


def canonical_topic(entity: str) -> str:
    return f"edis.canonical.{entity}.v1"


def dlq_topic(stage: str) -> str:
    return f"edis.dlq.{stage}.v1"


#: Maps every topic that carries a typed payload to its Pydantic model.
TOPIC_MODEL: dict[str, type[BaseModel]] = {
    RAW_SALES: IngestEnvelope,
    RAW_OPS: IngestEnvelope,
    RAW_CUSTOMER: IngestEnvelope,
    CANONICAL_ORDER: CanonicalEvent,
    CANONICAL_CUSTOMER: CanonicalEvent,
    CANONICAL_PRODUCT: CanonicalEvent,
    METRICS_POINTS: MetricPoint,
    FINDINGS: Finding,
    FORECASTS: Forecast,
    RECOMMENDATIONS: Recommendation,
    DECISIONS_LIFECYCLE: RecommendationLifecycleEvent,
    FEEDBACK_OUTCOMES: OutcomeReport,
    AUDIT: AuditEvent,
    LINEAGE: LineageEvent,
}
