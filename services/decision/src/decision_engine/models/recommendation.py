"""SQLAlchemy ORM for the L4 recommendation tables -- mirrors ``0001_decision`` exactly.

Three tables here (the static ``calibration_prior`` control-plane table is owned by the
L7 governance service and is NOT re-declared on the shared ``Base`` -- the decision engine
reads its prior through the in-memory ``CalibrationPriorProvider`` port):

* ``recommendation``            -- one row per edis.decisions.recommendations.v1
                                   Recommendation (deterministic impact/confidence/
                                   priority + the typed action + lifecycle status).
* ``recommendation_lifecycle``  -- one row per edis.decisions.lifecycle.v1 transition
                                   (proposed -> accepted/rejected/expired/...).
* ``outcome_report``            -- one row per edis.feedback.outcomes.v1 OutcomeReport,
                                   persisted by the NO-OP recorder (C2). The seam is
                                   present; nothing computes over it in the MVP.

Every model hangs off the shared :class:`edis_platform.db.session.Base` and **every
column mirrors the hand-authored DDL in ``alembic/versions/0001_decision.py``**: same
table names, columns, types, nullability, PKs, FK, and check constraints. The migration
owns the schema (it runs under ``@pytest.mark.integration``); this ORM is the typed
access layer the async repositories (C2) write through. Importing this module opens no
connection. Every table carries ``tenant_id`` (MVP isolation is app-level filtering; no
RLS FORCE). All timestamps are tz-aware UTC (``TIMESTAMP WITH TIME ZONE``).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from edis_platform.db.session import Base
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

#: The valid action_type values (mirrors the Recommendation contract literal).
_ACTION_TYPES = (
    "operational_fix",
    "pricing_change",
    "inventory_reallocation",
    "customer_outreach",
    "investigate",
    "scale_resource",
    "notify",
)
#: The valid recommendation status values (mirrors the contract literal).
_STATUSES = (
    "proposed",
    "accepted",
    "rejected",
    "expired",
    "in_progress",
    "outcome_recorded",
)
#: The valid effort tiers (mirrors the contract literal).
_EFFORT_TIERS = ("xs", "s", "m", "l", "xl")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    """Render a ``col IN ('a','b',...)`` clause matching the migration's DDL exactly."""

    quoted = ",".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


# ---------------------------------------------------------------------------
# recommendation  (mirrors 0001_decision.recommendation)
# ---------------------------------------------------------------------------
class RecommendationRow(Base):
    """One persisted ``edis.decisions.recommendations.v1`` Recommendation."""

    __tablename__ = "recommendation"
    __table_args__ = (
        PrimaryKeyConstraint("recommendation_id", name="pk_recommendation"),
        CheckConstraint(
            _in_list("action_type", _ACTION_TYPES),
            name="ck_recommendation_action_type",
        ),
        CheckConstraint(
            _in_list("effort_tier", _EFFORT_TIERS),
            name="ck_recommendation_effort_tier",
        ),
        CheckConstraint(
            _in_list("status", _STATUSES),
            name="ck_recommendation_status",
        ),
        Index("ix_recommendation_tenant", "tenant_id"),
        Index("ix_recommendation_tenant_status", "tenant_id", "status"),
        Index("ix_recommendation_tenant_created", "tenant_id", "created_at"),
        Index("ix_recommendation_tenant_rank", "tenant_id", "priority_rank"),
        Index("ix_recommendation_source_finding", "source_finding_id"),
    )

    recommendation_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_finding_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    playbook_id: Mapped[str] = mapped_column(Text, nullable=False)
    playbook_version: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    action_params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # ImpactEstimate (value/value_low/value_high/unit/direction/horizon_days/inputs/method).
    impact: Mapped[dict] = mapped_column(JSONB, nullable=False)
    effort_tier: Mapped[str] = mapped_column(Text, nullable=False)
    # ConfidenceScore (value/components/calibration_n).
    confidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    priority_score: Mapped[float] = mapped_column(Float, nullable=False)
    priority_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    explanation_summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_trail: Mapped[list] = mapped_column(JSONB, nullable=False)
    narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="proposed")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


# ---------------------------------------------------------------------------
# recommendation_lifecycle  (mirrors 0001_decision.recommendation_lifecycle)
# ---------------------------------------------------------------------------
class RecommendationLifecycleRow(Base):
    """One persisted ``edis.decisions.lifecycle.v1`` status transition."""

    __tablename__ = "recommendation_lifecycle"
    __table_args__ = (
        PrimaryKeyConstraint("event_id", name="pk_recommendation_lifecycle"),
        Index("ix_reclifecycle_tenant", "tenant_id"),
        Index("ix_reclifecycle_recommendation", "recommendation_id"),
        Index("ix_reclifecycle_recommendation_occurred", "recommendation_id", "occurred_at"),
    )

    event_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    from_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_status: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[dict] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


# ---------------------------------------------------------------------------
# outcome_report  (mirrors 0001_decision.outcome_report) -- no-op recorder target
# ---------------------------------------------------------------------------
class OutcomeReportRow(Base):
    """One persisted ``edis.feedback.outcomes.v1`` OutcomeReport (seam; no learning)."""

    __tablename__ = "outcome_report"
    __table_args__ = (
        PrimaryKeyConstraint("outcome_id", name="pk_outcome_report"),
        CheckConstraint(
            "source IN ('human','system','copilot')",
            name="ck_outcome_report_source",
        ),
        Index("ix_outcome_report_tenant", "tenant_id"),
        Index("ix_outcome_report_recommendation", "recommendation_id"),
    )

    outcome_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    realized_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
