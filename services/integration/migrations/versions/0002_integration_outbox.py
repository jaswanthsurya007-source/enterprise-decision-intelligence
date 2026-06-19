"""integration-layer bookkeeping: transactional outbox, idempotency, quarantine

Adds the three L2-owned operational tables that sit alongside the canonical
system-of-record from ``0001_canonical`` (which is left untouched):

  * ``integration_outbox``      -- the transactional outbox. The pipeline stages
    one row per event to publish in the SAME transaction as the canonical
    write; the relay (outbox/relay.py) reads ``published = false`` rows,
    publishes them via ``make_sink``, and flips the flag -> no
    persisted-but-not-published gap, exactly-once-ish with idempotent consumers.
  * ``integration_idempotency`` -- processed-envelope keys, so a replayed
    ``IngestEnvelope`` short-circuits to a DUPLICATE outcome without re-writing.
  * ``integration_quarantine``  -- persisted ``QuarantinedRecord``s (records that
    parsed but failed DQ, or were structurally un-mappable). Every input record
    terminates in exactly one of {canonical store, quarantine}.

Every table carries ``tenant_id`` (MVP isolation is application-level filtering).
All timestamps are tz-aware UTC (``TIMESTAMP WITH TIME ZONE``). The ORM models in
``edis_integration.persistence.models`` mirror this DDL exactly.

Revision ID: 0002_integration_outbox
Revises: 0001_canonical
Create Date: 2026-06-19 00:00:01.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_integration_outbox"
down_revision: str | None = "0001_canonical"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # integration_outbox -- the transactional outbox. Staged in the canonical
    # write txn; the relay publishes unpublished rows and marks them published.
    # ``event_id`` is unique so a re-staged event collapses (idempotent replay).
    # -------------------------------------------------------------------------
    op.create_table(
        "integration_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("published", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_integration_outbox"),
        sa.UniqueConstraint("event_id", name="uq_integration_outbox_event_id"),
    )
    # Partial-ish index that keeps the relay's "fetch unpublished, oldest-first"
    # scan cheap. (Composite over (published, created_at) is fine on any Postgres;
    # a partial WHERE published=false index is added as an additional fast path.)
    op.create_index(
        "ix_integration_outbox_unpublished",
        "integration_outbox",
        ["published", "created_at"],
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_integration_outbox_pending "
        "ON integration_outbox (created_at) WHERE published = false"
    )
    op.create_index("ix_integration_outbox_tenant", "integration_outbox", ["tenant_id"])

    # -------------------------------------------------------------------------
    # integration_idempotency -- processed-envelope keys. Composite PK on
    # (tenant_id, idempotency_key) so a replayed envelope is detected per tenant.
    # -------------------------------------------------------------------------
    op.create_table(
        "integration_idempotency",
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id", "idempotency_key", name="pk_integration_idempotency"
        ),
    )

    # -------------------------------------------------------------------------
    # integration_quarantine -- persisted QuarantinedRecords (DQ failures).
    # -------------------------------------------------------------------------
    op.create_table(
        "integration_quarantine",
        sa.Column("quarantine_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False, server_default="integration"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("dq_failures", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reprocessed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.PrimaryKeyConstraint("quarantine_id", name="pk_integration_quarantine"),
    )
    op.create_index(
        "ix_integration_quarantine_tenant_ts",
        "integration_quarantine",
        ["tenant_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_integration_quarantine_tenant_ts", table_name="integration_quarantine"
    )
    op.drop_table("integration_quarantine")

    op.drop_table("integration_idempotency")

    op.execute("DROP INDEX IF EXISTS ix_integration_outbox_pending")
    op.drop_index("ix_integration_outbox_tenant", table_name="integration_outbox")
    op.drop_index("ix_integration_outbox_unpublished", table_name="integration_outbox")
    op.drop_table("integration_outbox")
