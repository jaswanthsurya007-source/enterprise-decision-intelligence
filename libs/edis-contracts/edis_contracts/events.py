"""Canonical event payloads emitted by the integration layer.

Every ``canonical.*.v1`` topic is a *time-retained change feed* (not a
log-compacted snapshot store) carrying ``op in {created, updated, corrected}``,
so consumers (L3, copilot) can read canonical changes as an ordered stream.
``schema_version`` is ``int = 1`` on every payload, uniformly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class MetricPoint(BaseModel):
    """Payload of ``edis.metrics.points.v1`` -- the high-volume metric series topic."""

    tenant_id: str
    metric_key: str
    ts: datetime
    value: float
    dimensions: dict[str, str] = Field(default_factory=dict)
    unit: str | None = None
    source: str
    schema_version: int = 1


class CanonicalEvent(BaseModel):
    """Payload of ``edis.canonical.<entity>.v1`` -- one event type, ``op`` field.

    ``is_late`` is always ``False`` in the MVP (no watermark reordering);
    ``correction_of`` links a ``corrected`` event to the row it supersedes.
    """

    event_id: UUID
    tenant_id: str
    entity: Literal["customer", "product", "order"]
    op: Literal["created", "updated", "corrected"]
    occurred_at: datetime
    emitted_at: datetime
    canonical_id: UUID
    before: dict | None = None
    after: dict | None = None
    lineage_run_id: UUID | None = None
    is_late: bool = False
    correction_of: UUID | None = None
    schema_version: int = 1


class LineageEvent(BaseModel):
    """Payload of ``edis.governance.lineage.v1`` -- one run's input/output edges."""

    lineage_id: UUID
    tenant_id: str
    run_id: UUID
    inputs: list[dict] = Field(default_factory=list)  # [{type, id}]
    outputs: list[dict] = Field(default_factory=list)
    stage: str  # "integration" | "intelligence" | "decision"
    occurred_at: datetime
    schema_version: int = 1
