"""Decision-layer contracts: recommendations, lifecycle, outcomes.

All numbers (impact, confidence, priority) come from unit-tested code, never the
LLM. ``ConfidenceScore.calibration_n`` is 0 in the MVP (static prior); it becomes
> 0 once the feedback loop is built. ``schema_version`` is ``int`` everywhere.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ImpactEstimate(BaseModel):
    """Estimated business impact of an action, with the auditable inputs used."""

    value: float
    value_low: float
    value_high: float
    unit: str
    direction: Literal["increase", "decrease", "mitigate"]
    horizon_days: int
    inputs: dict[str, float] = Field(default_factory=dict)  # the retrieved facts used
    method: str


class ConfidenceScore(BaseModel):
    """Blended confidence. ``components`` keys: insight, evidence, historical_calibration."""

    value: float
    components: dict[str, float] = Field(default_factory=dict)
    calibration_n: int = 0  # 0 in MVP (static prior); >0 once feedback loop is built


class Recommendation(BaseModel):
    """Payload of ``edis.decisions.recommendations.v1`` -- a prioritized action."""

    recommendation_id: UUID
    tenant_id: str
    source_finding_id: UUID  # provenance back-link to the Finding
    playbook_id: str
    playbook_version: str
    title: str
    action_type: Literal[
        "operational_fix",
        "pricing_change",
        "inventory_reallocation",
        "customer_outreach",
        "investigate",
        "scale_resource",
        "notify",
    ]
    action_params: dict[str, Any] = Field(default_factory=dict)
    impact: ImpactEstimate
    effort_tier: Literal["xs", "s", "m", "l", "xl"]
    confidence: ConfidenceScore
    priority_score: float  # (impact.value * confidence.value) / effort_units
    priority_rank: int
    explanation_summary: str
    evidence_trail: list[dict] = Field(default_factory=list)
    narrative: str | None = None  # optional Opus prose; never numeric authority
    status: Literal[
        "proposed", "accepted", "rejected", "expired", "in_progress", "outcome_recorded"
    ] = "proposed"
    expires_at: datetime
    created_at: datetime
    schema_version: int = 1


class RecommendationLifecycleEvent(BaseModel):
    """Payload of ``edis.decisions.lifecycle.v1`` -- a status transition."""

    event_id: UUID
    tenant_id: str
    recommendation_id: UUID
    from_status: str | None = None
    to_status: str
    actor: dict = Field(default_factory=dict)  # {type, id}
    occurred_at: datetime
    schema_version: int = 1


class OutcomeReport(BaseModel):
    """Payload of ``edis.feedback.outcomes.v1`` (seam; recorder is no-op in MVP)."""

    outcome_id: UUID
    tenant_id: str
    recommendation_id: UUID
    source: Literal["human", "system", "copilot"]
    accepted: bool
    realized_value: float | None = None  # populated by the future feedback loop
    realized_unit: str | None = None
    notes: str | None = None
    occurred_at: datetime
    schema_version: int = 1
