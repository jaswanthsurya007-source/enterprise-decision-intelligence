"""governance spine: audit_log hypertable, lineage_edge, decision/evidence, control-plane

Creates the governance-layer (L7) schema per the EDIS governance/security
contracts (libs/edis-contracts/edis_contracts/governance.py, security.py):

  * audit_log        -- append-only TimescaleDB hypertable on ``occurred_at``;
                        idempotent on ``audit_id`` (the consumer dedupes on it).
                        One column per AuditEvent + a ``raw`` jsonb copy.
  * lineage_edge     -- materialized lineage graph (raw → canonical → metric →
                        finding → decision); the consumer fans a LineageEvent's
                        inputs × outputs out into edges.
  * decision         -- explainability record (Decision contract) + pgvector
                        ``embedding`` for "similar past decisions".
  * evidence         -- immutable value snapshots, FK → decision (Evidence contract).
  * control-plane    -- tenant, app_role, permission (RBAC), calibration_prior
                        (the pre-seeded static per-(tenant,playbook) prior).

Every table carries ``tenant_id`` (MVP isolation is application-level filtering;
**no RLS FORCE**). All timestamps are ``TIMESTAMP WITH TIME ZONE`` (UTC). The
timescaledb/vector extensions and every Timescale step are guarded so a plain
PostgreSQL still applies the core tables.

Revision ID: 0001_governance
Revises:
Create Date: 2026-06-19 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_governance"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# voyage-3 embedding dimensionality (pgvector "similar past decisions").
EMBEDDING_DIM = 1024


def _has_timescaledb() -> bool:
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
    ).first()
    return row is not None


def _has_vector() -> bool:
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    ).first()
    return row is not None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # Extensions — guarded so a plain Postgres still applies the core tables.
    # -------------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
            BEGIN
                CREATE EXTENSION IF NOT EXISTS timescaledb;
            EXCEPTION WHEN OTHERS THEN
                RAISE NOTICE 'timescaledb extension unavailable; audit_log stays plain';
            END;
            BEGIN
                CREATE EXTENSION IF NOT EXISTS vector;
            EXCEPTION WHEN OTHERS THEN
                RAISE NOTICE 'pgvector extension unavailable; decision.embedding stays jsonb';
            END;
        END
        $$;
        """
    )

    has_timescale = _has_timescaledb()
    has_vector = _has_vector()

    # -------------------------------------------------------------------------
    # Control-plane: tenant
    # -------------------------------------------------------------------------
    op.create_table(
        "tenant",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tenant"),
    )

    # -------------------------------------------------------------------------
    # Control-plane: app_role (RBAC role registry: viewer/analyst/operator/auditor/admin)
    # -------------------------------------------------------------------------
    op.create_table(
        "app_role",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("name", name="pk_app_role"),
    )

    # -------------------------------------------------------------------------
    # Control-plane: permission (role → action:resource_type), matching the
    # ROLE_PERMISSIONS table in edis_platform.authz.rbac.
    # -------------------------------------------------------------------------
    op.create_table(
        "permission",
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "role", "action", "resource_type", name="pk_permission"
        ),
        sa.ForeignKeyConstraint(
            ["role"], ["app_role.name"], name="fk_permission_role", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_permission_role", "permission", ["role"])

    # -------------------------------------------------------------------------
    # Control-plane: calibration_prior — pre-seeded static per-(tenant,playbook)
    # prior used by the Decision engine's ConfidenceScore (calibration_n=0 in MVP;
    # ``n`` becomes >0 once the feedback loop is built).
    # -------------------------------------------------------------------------
    op.create_table(
        "calibration_prior",
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("playbook_id", sa.Text(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id", "playbook_id", name="pk_calibration_prior"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name="fk_calibration_prior_tenant"
        ),
    )

    # -------------------------------------------------------------------------
    # decision — explainability record (Decision contract). pgvector embedding
    # for "similar past decisions" retrieval; degrades to JSONB without pgvector.
    # -------------------------------------------------------------------------
    op.create_table(
        "decision",
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("decision_type", sa.Text(), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.PrimaryKeyConstraint("decision_id", name="pk_decision"),
        sa.CheckConstraint(
            "decision_type IN ('finding_narrative','recommendation','copilot_answer')",
            name="ck_decision_type",
        ),
    )
    op.create_index("ix_decision_tenant", "decision", ["tenant_id"])
    op.create_index("ix_decision_tenant_subject", "decision", ["tenant_id", "subject_id"])
    if has_vector:
        op.execute(f"ALTER TABLE decision ADD COLUMN embedding vector({EMBEDDING_DIM})")
    else:
        op.add_column(
            "decision",
            sa.Column("embedding", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )

    # -------------------------------------------------------------------------
    # evidence — immutable value snapshot + live ref, FK → decision (Evidence).
    # -------------------------------------------------------------------------
    op.create_table(
        "evidence",
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.PrimaryKeyConstraint("evidence_id", name="pk_evidence"),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["decision.decision_id"],
            name="fk_evidence_decision",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_evidence_decision", "evidence", ["decision_id"])
    op.create_index("ix_evidence_tenant", "evidence", ["tenant_id"])

    # -------------------------------------------------------------------------
    # lineage_edge — materialized lineage graph. The lineage consumer fans a
    # LineageEvent's inputs × outputs into (src → dst) edges sharing run_id.
    # -------------------------------------------------------------------------
    op.create_table(
        "lineage_edge",
        sa.Column("lineage_edge_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("src_type", sa.Text(), nullable=False),
        sa.Column("src_id", sa.Text(), nullable=False),
        sa.Column("dst_type", sa.Text(), nullable=False),
        sa.Column("dst_id", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("lineage_edge_id", name="pk_lineage_edge"),
    )
    op.create_index("ix_lineage_edge_tenant", "lineage_edge", ["tenant_id"])
    op.create_index("ix_lineage_edge_run", "lineage_edge", ["run_id"])
    op.create_index(
        "ix_lineage_edge_src", "lineage_edge", ["tenant_id", "src_type", "src_id"]
    )
    op.create_index(
        "ix_lineage_edge_dst", "lineage_edge", ["tenant_id", "dst_type", "dst_id"]
    )

    # -------------------------------------------------------------------------
    # audit_log — append-only TimescaleDB hypertable on ``occurred_at``,
    # idempotent on ``audit_id``. A hypertable's unique/PK constraint MUST
    # include the partitioning column, so under Timescale the uniqueness is
    # (audit_id, occurred_at); on a plain Postgres the PK is audit_id alone.
    # Either way ``audit_id`` is the logical idempotency key the consumer dedupes
    # on (ON CONFLICT DO NOTHING).
    # -------------------------------------------------------------------------
    audit_columns = [
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("actor", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column(
            "schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "action IN ('DATA_READ','DATA_WRITE','AI_DECISION','AI_QUERY',"
            "'AUTH_DENY','RBAC_CHANGE','EXPORT')",
            name="ck_audit_log_action",
        ),
        sa.CheckConstraint(
            "outcome IN ('ALLOW','DENY','ERROR')", name="ck_audit_log_outcome"
        ),
    ]
    if has_timescale:
        # No PK on audit_id alone (Timescale forbids it); unique on (audit_id,
        # occurred_at) gives idempotency and includes the partitioning column.
        op.create_table(
            "audit_log",
            *audit_columns,
            sa.UniqueConstraint(
                "audit_id", "occurred_at", name="uq_audit_log_audit_id_time"
            ),
        )
        op.execute(
            """
            SELECT create_hypertable(
                'audit_log', 'occurred_at',
                chunk_time_interval => INTERVAL '7 days',
                if_not_exists => TRUE,
                migrate_data => TRUE
            );
            """
        )
    else:
        op.create_table(
            "audit_log",
            *audit_columns,
            sa.PrimaryKeyConstraint("audit_id", name="pk_audit_log"),
        )
    op.create_index("ix_audit_log_tenant", "audit_log", ["tenant_id"])
    op.create_index(
        "ix_audit_log_tenant_time", "audit_log", ["tenant_id", "occurred_at"]
    )
    op.create_index("ix_audit_log_action", "audit_log", ["tenant_id", "action"])
    op.create_index("ix_audit_log_decision", "audit_log", ["decision_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_decision", table_name="audit_log")
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_index("ix_audit_log_tenant_time", table_name="audit_log")
    op.drop_index("ix_audit_log_tenant", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index("ix_lineage_edge_dst", table_name="lineage_edge")
    op.drop_index("ix_lineage_edge_src", table_name="lineage_edge")
    op.drop_index("ix_lineage_edge_run", table_name="lineage_edge")
    op.drop_index("ix_lineage_edge_tenant", table_name="lineage_edge")
    op.drop_table("lineage_edge")

    op.drop_index("ix_evidence_tenant", table_name="evidence")
    op.drop_index("ix_evidence_decision", table_name="evidence")
    op.drop_table("evidence")

    op.drop_index("ix_decision_tenant_subject", table_name="decision")
    op.drop_index("ix_decision_tenant", table_name="decision")
    op.drop_table("decision")

    op.drop_table("calibration_prior")
    op.drop_index("ix_permission_role", table_name="permission")
    op.drop_table("permission")
    op.drop_table("app_role")
    op.drop_table("tenant")
