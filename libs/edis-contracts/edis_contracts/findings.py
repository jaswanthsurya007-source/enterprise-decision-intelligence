"""Intelligence-layer contracts: findings, candidate causes, evidence, forecasts.

The LLM never invents numbers. Every figure on a :class:`Finding` is computed by a
detector; the narrator may only reason over the :class:`EvidenceBundle`, whose
``allowed_numbers`` whitelist the grounding guard enforces.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class FindingKind(str, Enum):
    POINT_ANOMALY = "point_anomaly"
    SEASONAL_BREAK = "seasonal_break"
    LEVEL_SHIFT = "level_shift"
    TREND_BREAK = "trend_break"
    FORECAST_BREACH = "forecast_breach"
    ROOT_CAUSE = "root_cause"


class CandidateCause(BaseModel):
    """A lag-adjusted, ranked correlate of an anomaly (RCA output)."""

    metric_key: str
    dimensions: dict[str, str] = Field(default_factory=dict)
    correlation: float  # -1..1, lag-adjusted
    lag_minutes: int
    contribution_pct: float | None = None
    direction: Literal["leading", "coincident", "lagging"]
    observed_delta: float


class EvidenceItem(BaseModel):
    """One computed fact the narrator is permitted to cite."""

    kind: str  # "metric_window" | "candidate_cause" | "baseline" | "forecast"
    metric_key: str | None = None
    dimensions: dict[str, str] = Field(default_factory=dict)
    summary: str
    values: dict[str, float] = Field(default_factory=dict)
    ref: dict | None = None  # pointer to the source rows


class EvidenceBundle(BaseModel):
    """The *only* thing the LLM may reason over when narrating a finding."""

    bundle_id: UUID
    tenant_id: str
    finding_id: UUID
    created_at: datetime
    items: list[EvidenceItem] = Field(default_factory=list)
    allowed_numbers: list[float] = Field(default_factory=list)  # grounding-guard whitelist
    schema_version: int = 1


class Finding(BaseModel):
    """Payload of ``edis.findings.v1`` -- an atomic, computed detection.

    ``observed_value``/``expected_value``/``deviation`` are computed and the LLM
    never overrides them; ``narrative`` is null until grounded narration succeeds.
    """

    finding_id: UUID
    tenant_id: str
    kind: FindingKind
    metric_key: str
    dimensions: dict[str, str] = Field(default_factory=dict)
    window_start: datetime
    window_end: datetime
    detector: str
    detector_version: str
    observed_value: float
    expected_value: float
    deviation: float
    deviation_pct: float
    score: float  # detector-native (z-score / residual)
    severity: float  # 0..1 normalized
    confidence: float  # 0..1
    business_impact_input: float  # 0..1 -- Decision owns final ranking
    candidate_causes: list[CandidateCause] = Field(default_factory=list)
    narrative: str | None = None  # grounded LLM text; null until narrated
    narrative_model: str | None = None  # e.g. "claude-opus-4-8"
    evidence_ref: UUID | None = None  # FK to persisted EvidenceBundle (audit)
    status: Literal["open", "acknowledged", "resolved", "expired"] = "open"
    created_at: datetime
    schema_version: int = 1


class Forecast(BaseModel):
    """Payload of ``edis.forecasts.v1`` -- point + prediction interval per metric."""

    forecast_id: UUID
    tenant_id: str
    metric_key: str
    dimensions: dict[str, str] = Field(default_factory=dict)
    model: str  # "statsforecast.AutoETS"
    horizon_days: int
    points: list[dict] = Field(default_factory=list)  # [{ts, yhat, yhat_lower, yhat_upper}]
    generated_at: datetime
    schema_version: int = 1
