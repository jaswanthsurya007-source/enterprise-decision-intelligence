"""decision (L4) store: recommendation + lifecycle + outcome_report

Creates the decision-layer (L4) persistence schema per the EDIS contracts
(libs/edis-contracts/edis_contracts/decisions.py):

  * recommendation            -- one row per edis.decisions.recommendations.v1
                                 Recommendation. impact / confidence are stored as
                                 JSONB (the ImpactEstimate / ConfidenceScore shapes);
                                 priority_score / priority_rank are computed; status is
                                 the lifecycle state. ALL numbers come from the
                                 deterministic scoring core, never the LLM.
  * recommendation_lifecycle  -- one row per edis.decisions.lifecycle.v1 transition.
  * outcome_report            -- one row per edis.feedback.outcomes.v1 OutcomeReport,
                                 written by the NO-OP recorder (seam; nothing computes
                                 over it in the MVP -- the EMA recalibration is future).

The STATIC per-(tenant, playbook) ``calibration_prior`` that feeds
ConfidenceScore.components.historical_calibration is a control-plane table OWNED by the L7
governance migration (it carries an FK to ``tenant.id``); it is intentionally NOT created
here. The decision engine reads the prior through the in-memory CalibrationPriorProvider
port (calibration_n = 0 in the MVP; no live feedback loop).

Every table carries ``tenant_id`` (MVP isolation is application-level filtering; no RLS
FORCE). All timestamps are tz-aware UTC (``TIMESTAMP WITH TIME ZONE``). These are
ordinary relations -- no TimescaleDB hypertables and no pgvector needed -- so they apply
on a plain PostgreSQL. The ORM in ``decision_engine.models`` mirrors this DDL exactly.

Revision ID: 0001_decision
Revises:
Create Date: 2026-06-19 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_decision"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTION_TYPES = (
    "operational_fix",
    "pricing_change",
    "inventory_reallocation",
    "customer_outreach",
    "investigate",
    "scale_resource",
    "notify",
)
_STATUSES = (
    "proposed",
    "accepted",
    "rejected",
    "expired",
    "in_progress",
    "outcome_recorded",
)
_EFFORT_TIERS = ("xs", "s", "m", "l", "xl")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ",".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # recommendation — edis.decisions.recommendations.v1 payload, persisted.
    # impact / confidence are JSONB (the typed sub-models); priority_* + status
    # drive ranking + lifecycle. All numbers are computed (never from the LLM).
    # -------------------------------------------------------------------------
    op.create_table(
        "recommendation",
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("source_finding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("playbook_id", sa.Text(), nullable=False),
        sa.Column("playbook_version", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("action_params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("impact", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("effort_tier", sa.Text(), nullable=False),
        sa.Column("confidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("priority_score", sa.Float(), nullable=False),
        sa.Column("priority_rank", sa.Integer(), nullable=False),
        sa.Column("explanation_summary", sa.Text(), nullable=False),
        sa.Column("evidence_trail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="proposed"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.PrimaryKeyConstraint("recommendation_id", name="pk_recommendation"),
        sa.CheckConstraint(_in_list("action_type", _ACTION_TYPES), name="ck_recommendation_action_type"),
        sa.CheckConstraint(_in_list("effort_tier", _EFFORT_TIERS), name="ck_recommendation_effort_tier"),
        sa.CheckConstraint(_in_list("status", _STATUSES), name="ck_recommendation_status"),
    )
    op.create_index("ix_recommendation_tenant", "recommendation", ["tenant_id"])
    op.create_index("ix_recommendation_tenant_status", "recommendation", ["tenant_id", "status"])
    op.create_index("ix_recommendation_tenant_created", "recommendation", ["tenant_id", "created_at"])
    op.create_index("ix_recommendation_tenant_rank", "recommendation", ["tenant_id", "priority_rank"])
    op.create_index("ix_recommendation_source_finding", "recommendation", ["source_finding_id"])

    # -------------------------------------------------------------------------
    # recommendation_lifecycle — edis.decisions.lifecycle.v1 transitions.
    # -------------------------------------------------------------------------
    op.create_table(
        "recommendation_lifecycle",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("actor", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.PrimaryKeyConstraint("event_id", name="pk_recommendation_lifecycle"),
    )
    op.create_index("ix_reclifecycle_tenant", "recommendation_lifecycle", ["tenant_id"])
    op.create_index(
        "ix_reclifecycle_recommendation", "recommendation_lifecycle", ["recommendation_id"]
    )
    op.create_index(
        "ix_reclifecycle_recommendation_occurred",
        "recommendation_lifecycle",
        ["recommendation_id", "occurred_at"],
    )

    # -------------------------------------------------------------------------
    # outcome_report — edis.feedback.outcomes.v1 payload. Written by the no-op
    # recorder; nothing computes over it in the MVP (the seam for the feedback loop).
    # -------------------------------------------------------------------------
    op.create_table(
        "outcome_report",
        sa.Column("outcome_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("realized_value", sa.Float(), nullable=True),
        sa.Column("realized_unit", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.PrimaryKeyConstraint("outcome_id", name="pk_outcome_report"),
        sa.CheckConstraint(
            "source IN ('human','system','copilot')", name="ck_outcome_report_source"
        ),
    )
    op.create_index("ix_outcome_report_tenant", "outcome_report", ["tenant_id"])
    op.create_index(
        "ix_outcome_report_recommendation", "outcome_report", ["recommendation_id"]
    )

    # NOTE: the STATIC ``calibration_prior`` control-plane table (FK -> tenant.id) is
    # owned and created by the L7 governance migration; it is intentionally NOT created
    # here so the two services do not collide on table ownership / shared metadata.


def downgrade() -> None:
    op.drop_index("ix_outcome_report_recommendation", table_name="outcome_report")
    op.drop_index("ix_outcome_report_tenant", table_name="outcome_report")
    op.drop_table("outcome_report")

    op.drop_index(
        "ix_reclifecycle_recommendation_occurred", table_name="recommendation_lifecycle"
    )
    op.drop_index("ix_reclifecycle_recommendation", table_name="recommendation_lifecycle")
    op.drop_index("ix_reclifecycle_tenant", table_name="recommendation_lifecycle")
    op.drop_table("recommendation_lifecycle")

    op.drop_index("ix_recommendation_source_finding", table_name="recommendation")
    op.drop_index("ix_recommendation_tenant_rank", table_name="recommendation")
    op.drop_index("ix_recommendation_tenant_created", table_name="recommendation")
    op.drop_index("ix_recommendation_tenant_status", table_name="recommendation")
    op.drop_index("ix_recommendation_tenant", table_name="recommendation")
    op.drop_table("recommendation")
