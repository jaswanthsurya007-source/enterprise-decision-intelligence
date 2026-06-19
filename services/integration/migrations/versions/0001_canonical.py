"""canonical model + metric_observations hypertable + continuous aggregate + pgvector

Creates the integration-layer (L2) system-of-record schema per the EDIS
canonical contracts (libs/edis-contracts/edis_contracts/canonical.py):

  * canonical_customer / canonical_product  -- SCD-2-shaped dimensions
  * canonical_order (+ canonical_order_line) -- immutable sales facts
  * ops_event                                -- immutable ops facts (error_rate / latency_p95)
  * customer_activity                        -- immutable activity facts (page_views / sessions)
  * metric_observations                      -- TimescaleDB hypertable on ``ts``
  * metric_observations_daily                -- daily continuous aggregate rollup

Every table carries ``tenant_id`` (MVP isolation is application-level filtering;
no RLS FORCE). All timestamps are tz-aware UTC (``TIMESTAMP WITH TIME ZONE``).

The ``timescaledb`` and ``vector`` extensions are created with
``CREATE EXTENSION IF NOT EXISTS`` and every Timescale-specific step
(create_hypertable, continuous aggregate, retention) is wrapped in a guard so a
plain PostgreSQL without TimescaleDB/pgvector still applies the core tables
(metric_observations remains an ordinary table; the embedding column degrades to
JSON when pgvector is absent).

Revision ID: 0001_canonical
Revises:
Create Date: 2026-06-19 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_canonical"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# voyage-3 embedding dimensionality (used by L3/Copilot pgvector retrieval).
EMBEDDING_DIM = 1024


def _has_timescaledb() -> bool:
    """Return True if the timescaledb extension is installed and available."""

    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
    ).first()
    return row is not None


def _has_vector() -> bool:
    """Return True if the pgvector extension is installed and available."""

    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    ).first()
    return row is not None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # Extensions — guarded so a plain Postgres (no Timescale/pgvector) still
    # applies the core tables. Each statement is independently swallowed: a node
    # may have pgvector but not TimescaleDB, or vice versa.
    # -------------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
            BEGIN
                CREATE EXTENSION IF NOT EXISTS timescaledb;
            EXCEPTION WHEN OTHERS THEN
                RAISE NOTICE 'timescaledb extension unavailable; metric tables stay plain';
            END;
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
    # canonical_customer — SCD-2-shaped dimension (MVP: always is_current=true,
    # valid_to=null, version=1). PK is the surrogate canonical_customer_id.
    # -------------------------------------------------------------------------
    op.create_table(
        "canonical_customer",
        sa.Column("canonical_customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("legal_name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("primary_email", sa.Text(), nullable=True),
        sa.Column("country_iso2", sa.String(length=2), nullable=True),
        sa.Column("industry", sa.Text(), nullable=True),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("source_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dq_score", sa.Float(), nullable=False),
        sa.Column("record_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("canonical_customer_id", name="pk_canonical_customer"),
    )
    op.create_index(
        "ix_canonical_customer_tenant", "canonical_customer", ["tenant_id"]
    )
    op.create_index(
        "ix_canonical_customer_tenant_current",
        "canonical_customer",
        ["tenant_id", "is_current"],
    )

    # -------------------------------------------------------------------------
    # canonical_product — SCD-2-shaped dimension.
    # -------------------------------------------------------------------------
    op.create_table(
        "canonical_product",
        sa.Column("canonical_product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("uom", sa.Text(), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("source_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("record_hash", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("canonical_product_id", name="pk_canonical_product"),
    )
    op.create_index("ix_canonical_product_tenant", "canonical_product", ["tenant_id"])
    op.create_index(
        "ix_canonical_product_tenant_sku", "canonical_product", ["tenant_id", "sku"]
    )

    # -------------------------------------------------------------------------
    # canonical_order — immutable sales fact, normalized to base currency (USD).
    # -------------------------------------------------------------------------
    op.create_table(
        "canonical_order",
        sa.Column("canonical_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("canonical_customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency_base", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("amount_base", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("amount_src", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("currency_src", sa.String(length=3), nullable=False),
        sa.Column("fx_rate", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("channel", sa.Text(), nullable=True),
        sa.Column("source_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("record_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("canonical_order_id", name="pk_canonical_order"),
        sa.ForeignKeyConstraint(
            ["canonical_customer_id"],
            ["canonical_customer.canonical_customer_id"],
            name="fk_canonical_order_customer",
        ),
        sa.CheckConstraint(
            "channel IS NULL OR channel IN ('web','partner','direct')",
            name="ck_canonical_order_channel",
        ),
    )
    op.create_index("ix_canonical_order_tenant", "canonical_order", ["tenant_id"])
    op.create_index(
        "ix_canonical_order_tenant_ts", "canonical_order", ["tenant_id", "order_ts"]
    )
    op.create_index(
        "ix_canonical_order_customer", "canonical_order", ["canonical_customer_id"]
    )

    # -------------------------------------------------------------------------
    # canonical_order_line — one row per CanonicalOrderLine on an order. Carries
    # tenant_id (every table does) and a surrogate PK; FK back to the order.
    # -------------------------------------------------------------------------
    op.create_table(
        "canonical_order_line",
        sa.Column(
            "canonical_order_line_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("canonical_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("canonical_product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("unit_price_base", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("line_amount_base", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.PrimaryKeyConstraint("canonical_order_line_id", name="pk_canonical_order_line"),
        sa.ForeignKeyConstraint(
            ["canonical_order_id"],
            ["canonical_order.canonical_order_id"],
            name="fk_canonical_order_line_order",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "canonical_order_id", "line_no", name="uq_canonical_order_line_no"
        ),
    )
    op.create_index(
        "ix_canonical_order_line_tenant", "canonical_order_line", ["tenant_id"]
    )
    op.create_index(
        "ix_canonical_order_line_order", "canonical_order_line", ["canonical_order_id"]
    )

    # -------------------------------------------------------------------------
    # ops_event — immutable ops fact feeding error_rate / latency_p95 metrics.
    # -------------------------------------------------------------------------
    op.create_table(
        "ops_event",
        sa.Column("canonical_ops_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("event_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("record_hash", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("canonical_ops_event_id", name="pk_ops_event"),
        sa.CheckConstraint(
            "level IN ('info','warn','error')", name="ck_ops_event_level"
        ),
    )
    op.create_index("ix_ops_event_tenant", "ops_event", ["tenant_id"])
    op.create_index(
        "ix_ops_event_tenant_service_ts",
        "ops_event",
        ["tenant_id", "service", "event_ts"],
    )

    # -------------------------------------------------------------------------
    # customer_activity — immutable activity fact feeding page_views / sessions.
    # -------------------------------------------------------------------------
    op.create_table(
        "customer_activity",
        sa.Column("canonical_activity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("canonical_customer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("channel", sa.Text(), nullable=True),
        sa.Column("props", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("event_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("record_hash", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("canonical_activity_id", name="pk_customer_activity"),
        sa.ForeignKeyConstraint(
            ["canonical_customer_id"],
            ["canonical_customer.canonical_customer_id"],
            name="fk_customer_activity_customer",
        ),
        sa.CheckConstraint(
            "channel IS NULL OR channel IN ('web','partner','direct')",
            name="ck_customer_activity_channel",
        ),
    )
    op.create_index("ix_customer_activity_tenant", "customer_activity", ["tenant_id"])
    op.create_index(
        "ix_customer_activity_tenant_session",
        "customer_activity",
        ["tenant_id", "session_id"],
    )
    op.create_index(
        "ix_customer_activity_tenant_ts",
        "customer_activity",
        ["tenant_id", "event_ts"],
    )

    # -------------------------------------------------------------------------
    # metric_observations — TimescaleDB hypertable on ``ts``. This is the
    # high-volume metric series store (MetricObservation contract). A pgvector
    # ``embedding`` column is reserved for L3/Copilot retrieval (voyage-3, 1024d);
    # it degrades to JSONB when pgvector is absent.
    #
    # NOTE: a hypertable's PK / unique constraints MUST include the partitioning
    # column (``ts``), so the composite PK is (tenant_id, metric_key, ts).
    # -------------------------------------------------------------------------
    op.create_table(
        "metric_observations",
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("metric_key", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dimensions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("source_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id", "metric_key", "ts", name="pk_metric_observations"
        ),
    )
    # pgvector embedding column (voyage-3, 1024d) for L3/Copilot retrieval. Added
    # via raw DDL so the migration never imports the optional pgvector Python
    # package; degrades to JSONB on a plain Postgres without pgvector.
    if has_vector:
        op.execute(
            f"ALTER TABLE metric_observations "
            f"ADD COLUMN embedding vector({EMBEDDING_DIM})"
        )
    else:
        op.add_column(
            "metric_observations",
            sa.Column("embedding", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )
    op.create_index(
        "ix_metric_observations_tenant_key_ts",
        "metric_observations",
        ["tenant_id", "metric_key", "ts"],
    )
    # GIN index over the dimension map so {region,channel,service} filters are fast.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_metric_observations_dimensions "
        "ON metric_observations USING gin (dimensions)"
    )

    # -------------------------------------------------------------------------
    # Promote metric_observations to a TimescaleDB hypertable + add a daily
    # continuous aggregate. Guarded: on a plain Postgres these no-op and the
    # table remains an ordinary (still fully usable) relation.
    # -------------------------------------------------------------------------
    if _has_timescaledb():
        op.execute(
            """
            SELECT create_hypertable(
                'metric_observations', 'ts',
                chunk_time_interval => INTERVAL '7 days',
                if_not_exists => TRUE,
                migrate_data => TRUE
            );
            """
        )
        # Daily continuous aggregate: per (tenant, metric, day) rollup used for
        # dashboard KPIs and L3 baselines. time_bucket on the hypertable time col.
        op.execute(
            """
            CREATE MATERIALIZED VIEW IF NOT EXISTS metric_observations_daily
            WITH (timescaledb.continuous) AS
            SELECT
                tenant_id,
                metric_key,
                time_bucket('1 day', ts) AS bucket,
                avg(value)   AS avg_value,
                sum(value)   AS sum_value,
                min(value)   AS min_value,
                max(value)   AS max_value,
                count(*)     AS sample_count
            FROM metric_observations
            GROUP BY tenant_id, metric_key, time_bucket('1 day', ts)
            WITH NO DATA;
            """
        )
        # Keep the aggregate fresh (no-op without the Timescale job scheduler).
        op.execute(
            """
            DO $$
            BEGIN
                PERFORM add_continuous_aggregate_policy(
                    'metric_observations_daily',
                    start_offset      => INTERVAL '30 days',
                    end_offset        => INTERVAL '1 hour',
                    schedule_interval => INTERVAL '1 hour',
                    if_not_exists     => TRUE
                );
            EXCEPTION WHEN OTHERS THEN
                RAISE NOTICE 'continuous aggregate policy not scheduled (job scheduler off)';
            END
            $$;
            """
        )
    else:
        # Plain-Postgres fallback: a regular view with the same shape so callers
        # that read metric_observations_daily work (without incremental refresh).
        op.execute(
            """
            CREATE OR REPLACE VIEW metric_observations_daily AS
            SELECT
                tenant_id,
                metric_key,
                date_trunc('day', ts) AS bucket,
                avg(value)   AS avg_value,
                sum(value)   AS sum_value,
                min(value)   AS min_value,
                max(value)   AS max_value,
                count(*)     AS sample_count
            FROM metric_observations
            GROUP BY tenant_id, metric_key, date_trunc('day', ts);
            """
        )


def downgrade() -> None:
    # Drop the daily aggregate first (works for both the continuous aggregate
    # MATERIALIZED VIEW and the plain VIEW fallback).
    op.execute("DROP MATERIALIZED VIEW IF EXISTS metric_observations_daily CASCADE")
    op.execute("DROP VIEW IF EXISTS metric_observations_daily CASCADE")

    op.drop_index("ix_metric_observations_dimensions", table_name="metric_observations")
    op.drop_index(
        "ix_metric_observations_tenant_key_ts", table_name="metric_observations"
    )
    op.drop_table("metric_observations")

    op.drop_index("ix_customer_activity_tenant_ts", table_name="customer_activity")
    op.drop_index("ix_customer_activity_tenant_session", table_name="customer_activity")
    op.drop_index("ix_customer_activity_tenant", table_name="customer_activity")
    op.drop_table("customer_activity")

    op.drop_index("ix_ops_event_tenant_service_ts", table_name="ops_event")
    op.drop_index("ix_ops_event_tenant", table_name="ops_event")
    op.drop_table("ops_event")

    op.drop_index("ix_canonical_order_line_order", table_name="canonical_order_line")
    op.drop_index("ix_canonical_order_line_tenant", table_name="canonical_order_line")
    op.drop_table("canonical_order_line")

    op.drop_index("ix_canonical_order_customer", table_name="canonical_order")
    op.drop_index("ix_canonical_order_tenant_ts", table_name="canonical_order")
    op.drop_index("ix_canonical_order_tenant", table_name="canonical_order")
    op.drop_table("canonical_order")

    op.drop_index("ix_canonical_product_tenant_sku", table_name="canonical_product")
    op.drop_index("ix_canonical_product_tenant", table_name="canonical_product")
    op.drop_table("canonical_product")

    op.drop_index("ix_canonical_customer_tenant_current", table_name="canonical_customer")
    op.drop_index("ix_canonical_customer_tenant", table_name="canonical_customer")
    op.drop_table("canonical_customer")
