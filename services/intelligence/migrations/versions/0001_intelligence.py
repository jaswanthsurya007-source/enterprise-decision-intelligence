"""intelligence (L3) store: findings + forecasts + evidence_bundle (+guarded pgvector)

Creates the intelligence-layer (L3) persistence schema per the EDIS contracts
(libs/edis-contracts/edis_contracts/findings.py):

  * findings         -- one row per edis.findings.v1 Finding (computed detection +
                        normalized severity/confidence/business_impact_input,
                        candidate_causes, grounded narrative, status). Carries a
                        pgvector ``embedding`` column (voyage-3, 1024d) so the
                        copilot can semantically retrieve past findings.
  * forecasts        -- one row per edis.forecasts.v1 Forecast (the single ETS
                        prediction band; points stored as JSONB).
  * evidence_bundle  -- the EvidenceBundle the narrator reasoned over: the items
                        (computed facts) and the ``allowed_numbers`` whitelist the
                        grounding guard enforces. FK target of findings.evidence_ref.

Every table carries ``tenant_id`` (MVP isolation is application-level filtering;
no RLS FORCE). All timestamps are tz-aware UTC (``TIMESTAMP WITH TIME ZONE``).

The ``vector`` extension is created with ``CREATE EXTENSION IF NOT EXISTS`` wrapped
in an exception guard, and the embedding column is added as ``vector(1024)`` only
when pgvector is present, degrading to JSONB otherwise -- so a plain PostgreSQL
without pgvector still applies every table. (TimescaleDB is not needed here; these
are ordinary relations, not hypertables.)

Revision ID: 0001_intelligence
Revises:
Create Date: 2026-06-19 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_intelligence"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# voyage-3 embedding dimensionality (matches IntelligenceSettings.embedding_dim
# and the metric_observations embedding column in the L2 migration).
EMBEDDING_DIM = 1024


def _has_vector() -> bool:
    """Return True if the pgvector extension is installed and available."""

    bind = op.get_bind()
    row = bind.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).first()
    return row is not None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # Extension — guarded so a plain Postgres (no pgvector) still applies the
    # core tables; the embedding column then degrades to JSONB.
    # -------------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
            BEGIN
                CREATE EXTENSION IF NOT EXISTS vector;
            EXCEPTION WHEN OTHERS THEN
                RAISE NOTICE 'pgvector extension unavailable; embedding columns stay jsonb';
            END;
        END
        $$;
        """
    )

    has_vector = _has_vector()

    # -------------------------------------------------------------------------
    # evidence_bundle — the ONLY thing the narrator may reason over. Created
    # before ``findings`` so findings.evidence_ref can FK to it.
    # -------------------------------------------------------------------------
    op.create_table(
        "evidence_bundle",
        sa.Column("bundle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("finding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # EvidenceItem[] (kind/metric_key/dimensions/summary/values/ref).
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        # allowed_numbers whitelist the grounding guard enforces.
        sa.Column("allowed_numbers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.PrimaryKeyConstraint("bundle_id", name="pk_evidence_bundle"),
    )
    op.create_index("ix_evidence_bundle_tenant", "evidence_bundle", ["tenant_id"])
    op.create_index("ix_evidence_bundle_finding", "evidence_bundle", ["finding_id"])

    # -------------------------------------------------------------------------
    # findings — edis.findings.v1 payload, persisted. observed/expected/deviation
    # and the detector-native score are COMPUTED (the LLM never overrides them);
    # narrative/narrative_model are null until grounded narration succeeds.
    # -------------------------------------------------------------------------
    op.create_table(
        "findings",
        sa.Column("finding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("metric_key", sa.Text(), nullable=False),
        sa.Column("dimensions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detector", sa.Text(), nullable=False),
        sa.Column("detector_version", sa.Text(), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=False),
        sa.Column("expected_value", sa.Float(), nullable=False),
        sa.Column("deviation", sa.Float(), nullable=False),
        sa.Column("deviation_pct", sa.Float(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("severity", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("business_impact_input", sa.Float(), nullable=False),
        sa.Column("candidate_causes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=True),
        sa.Column("narrative_model", sa.Text(), nullable=True),
        sa.Column("evidence_ref", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.PrimaryKeyConstraint("finding_id", name="pk_findings"),
        sa.ForeignKeyConstraint(
            ["evidence_ref"],
            ["evidence_bundle.bundle_id"],
            name="fk_findings_evidence",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "kind IN ('point_anomaly','seasonal_break','level_shift',"
            "'trend_break','forecast_breach','root_cause')",
            name="ck_findings_kind",
        ),
        sa.CheckConstraint(
            "status IN ('open','acknowledged','resolved','expired')",
            name="ck_findings_status",
        ),
    )
    op.create_index("ix_findings_tenant", "findings", ["tenant_id"])
    op.create_index(
        "ix_findings_tenant_metric_window",
        "findings",
        ["tenant_id", "metric_key", "window_start"],
    )
    op.create_index("ix_findings_tenant_status", "findings", ["tenant_id", "status"])
    op.create_index("ix_findings_tenant_created", "findings", ["tenant_id", "created_at"])
    # GIN over the dimension map so {region,channel,service} filters are fast.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_findings_dimensions "
        "ON findings USING gin (dimensions)"
    )

    # pgvector embedding column (voyage-3, 1024d) for copilot semantic retrieval;
    # added via raw DDL so the migration never imports the optional pgvector
    # Python package. Degrades to JSONB on a plain Postgres without pgvector.
    if has_vector:
        op.execute(f"ALTER TABLE findings ADD COLUMN embedding vector({EMBEDDING_DIM})")
    else:
        op.add_column(
            "findings",
            sa.Column("embedding", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )

    # -------------------------------------------------------------------------
    # forecasts — edis.forecasts.v1 payload. The single ETS band; ``points`` is a
    # JSONB array of {ts, yhat, yhat_lower, yhat_upper}.
    # -------------------------------------------------------------------------
    op.create_table(
        "forecasts",
        sa.Column("forecast_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("metric_key", sa.Text(), nullable=False),
        sa.Column("dimensions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("points", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.PrimaryKeyConstraint("forecast_id", name="pk_forecasts"),
    )
    op.create_index("ix_forecasts_tenant", "forecasts", ["tenant_id"])
    op.create_index(
        "ix_forecasts_tenant_metric_generated",
        "forecasts",
        ["tenant_id", "metric_key", "generated_at"],
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_forecasts_dimensions "
        "ON forecasts USING gin (dimensions)"
    )


def downgrade() -> None:
    op.drop_index("ix_forecasts_dimensions", table_name="forecasts")
    op.drop_index("ix_forecasts_tenant_metric_generated", table_name="forecasts")
    op.drop_index("ix_forecasts_tenant", table_name="forecasts")
    op.drop_table("forecasts")

    op.drop_index("ix_findings_dimensions", table_name="findings")
    op.drop_index("ix_findings_tenant_created", table_name="findings")
    op.drop_index("ix_findings_tenant_status", table_name="findings")
    op.drop_index("ix_findings_tenant_metric_window", table_name="findings")
    op.drop_index("ix_findings_tenant", table_name="findings")
    op.drop_table("findings")

    op.drop_index("ix_evidence_bundle_finding", table_name="evidence_bundle")
    op.drop_index("ix_evidence_bundle_tenant", table_name="evidence_bundle")
    op.drop_table("evidence_bundle")
