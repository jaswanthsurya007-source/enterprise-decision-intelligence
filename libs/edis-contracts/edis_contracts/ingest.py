"""L1 -> L2 ingestion contracts.

The :class:`IngestEnvelope` is the stable boundary between *untrusted source
reality* and *the platform*. Per-domain payloads are validated at the edge with
``extra="forbid"`` so source schema drift is caught the moment it arrives.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Domain = Literal["sales", "ops", "customer"]


class SalesPayloadV1(BaseModel):
    """Validated source sales record (currency/timestamps already coerced)."""

    model_config = ConfigDict(extra="forbid")

    order_id: str
    customer_id: str
    sku: str
    qty: int
    unit_price: float
    currency: str = "USD"
    region: str | None = None
    channel: str | None = None
    order_ts: datetime


class OpsPayloadV1(BaseModel):
    """Validated operations-log record (feeds error_rate / latency_p95)."""

    model_config = ConfigDict(extra="forbid")

    service: str
    region: str | None = None
    level: Literal["info", "warn", "error"] = "info"
    status_code: int | None = None
    latency_ms: float | None = None
    message: str | None = None
    event_ts: datetime


class CustomerPayloadV1(BaseModel):
    """Validated customer-activity record (feeds page_views / sessions)."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str | None = None
    session_id: str
    event: str
    region: str | None = None
    channel: str | None = None
    props: dict[str, str] = Field(default_factory=dict)
    event_ts: datetime


class IngestEnvelope(BaseModel):
    """The one envelope every record entering the bus is wrapped in.

    ``idempotency_key`` is deterministic and replay-safe; ``trace_context``
    carries the W3C traceparent so a single record is traceable across all
    seven layers.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    idempotency_key: str
    schema_ref: str
    domain: Domain
    tenant_id: str
    source_system: str
    ingest_ts: datetime
    event_ts: datetime
    producer: str = "ingestion"
    trace_context: dict[str, str] = Field(default_factory=dict)
    is_synthetic: bool = True
    anomaly_label: str | None = None
    payload: dict[str, Any]
    schema_version: int = 1


class DLQRecord(BaseModel):
    """Dead-letter record for data that failed validation at the ingestion edge."""

    model_config = ConfigDict(extra="forbid")

    dlq_id: UUID
    tenant_id: str | None = None
    stage: Literal["ingest", "integration"] = "ingest"
    domain: Domain | None = None
    source_system: str | None = None
    raw: Any = None
    error_type: str
    error_detail: str
    occurred_at: datetime
    trace_id: str | None = None
    schema_version: int = 1


class QuarantinedRecord(BaseModel):
    """Record that parsed but failed integration-layer data-quality checks.

    Every input record terminates in exactly one of {canonical store, quarantine}
    -- never silently dropped, never double-counted.
    """

    model_config = ConfigDict(extra="forbid")

    quarantine_id: UUID
    tenant_id: str
    stage: Literal["integration"] = "integration"
    reason: str
    dq_failures: list[str] = Field(default_factory=list)
    raw: Any = None
    occurred_at: datetime
    schema_version: int = 1
