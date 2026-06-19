"""Ingestion landing store: raw_events (outbox), ingest_dlq, ingest_checkpoint.

Revision ID: 0001_ingestion
Revises:
Create Date: 2026-06-19

Mirrors :mod:`ingestion.storage.models`. ``raw_events.idempotency_key`` is unique
(the DB-level dedupe backstop behind the Redis SETNX guard); a partial-friendly
index on ``published`` supports the outbox reconcile relay.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_ingestion"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "raw_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("domain", sa.String(length=32), nullable=False),
        sa.Column("source_system", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("anomaly_label", sa.String(length=64), nullable=True),
        sa.Column("ingest_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_raw_events_idempotency_key"),
    )
    op.create_index(
        "ix_raw_events_tenant_domain_ts",
        "raw_events",
        ["tenant_id", "domain", "event_ts"],
    )
    op.create_index("ix_raw_events_unpublished", "raw_events", ["published"])

    op.create_table(
        "ingest_dlq",
        sa.Column("dlq_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
        sa.Column("stage", sa.String(length=32), nullable=False, server_default="ingest"),
        sa.Column("domain", sa.String(length=32), nullable=True),
        sa.Column("source_system", sa.String(length=128), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_type", sa.String(length=128), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("replayed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_ingest_dlq_tenant_ts", "ingest_dlq", ["tenant_id", "occurred_at"])

    op.create_table(
        "ingest_checkpoint",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("source_key", sa.String(length=1024), nullable=False),
        sa.Column("offset", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_ingested", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "source_key", name="uq_ingest_checkpoint_source"),
    )


def downgrade() -> None:
    op.drop_table("ingest_checkpoint")
    op.drop_index("ix_ingest_dlq_tenant_ts", table_name="ingest_dlq")
    op.drop_table("ingest_dlq")
    op.drop_index("ix_raw_events_unpublished", table_name="raw_events")
    op.drop_index("ix_raw_events_tenant_domain_ts", table_name="raw_events")
    op.drop_table("raw_events")
