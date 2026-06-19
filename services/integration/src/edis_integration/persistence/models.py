"""SQLAlchemy ORM models for the L2 canonical system-of-record.

Every model hangs off the shared :class:`edis_platform.db.session.Base` so all
services share one declarative registry, and **every column mirrors the
hand-authored DDL in ``migrations/versions/0001_canonical.py`` exactly** -- same
table names, column names, types, nullability, primary keys, and check
constraints. The migrations own the schema (they are the source of truth and run
under ``@pytest.mark.integration``); these ORM models are the typed access layer
the async repositories write through. Importing this module opens no connection.

The canonical tables (``0001_canonical``):

* ``canonical_customer`` / ``canonical_product`` -- SCD-2-shaped dimensions.
* ``canonical_order`` (+ ``canonical_order_line``) -- immutable sales facts.
* ``ops_event``                                   -- immutable ops facts.
* ``customer_activity``                           -- immutable activity facts.
* ``metric_observations``                         -- the metric series (Timescale
  hypertable in prod; an ordinary table without TimescaleDB). The optional
  ``embedding`` column is **not** mapped here -- it is owned by L3/Copilot and the
  integration layer never writes it -- so this ORM stays agnostic to whether
  pgvector promoted it to ``vector`` or left it ``jsonb``.

The L2-owned bookkeeping tables (``0002_integration_outbox``):

* ``integration_outbox``     -- the transactional outbox (events staged in the
  same txn as the canonical write; the relay publishes + marks them).
* ``integration_idempotency`` -- processed-envelope keys for idempotent replay.
* ``integration_quarantine`` -- persisted ``QuarantinedRecord``s (DQ failures).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from edis_platform.db.session import Base
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column


# ---------------------------------------------------------------------------
# canonical_customer  (mirrors 0001_canonical.canonical_customer)
# ---------------------------------------------------------------------------
class CanonicalCustomerRow(Base):
    """SCD-2-shaped customer dimension (MVP: is_current=true, valid_to=null, v=1)."""

    __tablename__ = "canonical_customer"
    __table_args__ = (
        PrimaryKeyConstraint("canonical_customer_id", name="pk_canonical_customer"),
        Index("ix_canonical_customer_tenant", "tenant_id"),
        Index("ix_canonical_customer_tenant_current", "tenant_id", "is_current"),
    )

    canonical_customer_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    primary_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    country_iso2: Mapped[str | None] = mapped_column(String(2), nullable=True)
    industry: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    dq_score: Mapped[float] = mapped_column(Float, nullable=False)
    record_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# canonical_product  (mirrors 0001_canonical.canonical_product)
# ---------------------------------------------------------------------------
class CanonicalProductRow(Base):
    """SCD-2-shaped product dimension."""

    __tablename__ = "canonical_product"
    __table_args__ = (
        PrimaryKeyConstraint("canonical_product_id", name="pk_canonical_product"),
        Index("ix_canonical_product_tenant", "tenant_id"),
        Index("ix_canonical_product_tenant_sku", "tenant_id", "sku"),
    )

    canonical_product_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    uom: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    record_hash: Mapped[str] = mapped_column(Text, nullable=False)


# ---------------------------------------------------------------------------
# canonical_order  (mirrors 0001_canonical.canonical_order)
# ---------------------------------------------------------------------------
class CanonicalOrderRow(Base):
    """Immutable sales fact, normalized to base currency (USD)."""

    __tablename__ = "canonical_order"
    __table_args__ = (
        PrimaryKeyConstraint("canonical_order_id", name="pk_canonical_order"),
        CheckConstraint(
            "channel IS NULL OR channel IN ('web','partner','direct')",
            name="ck_canonical_order_channel",
        ),
        Index("ix_canonical_order_tenant", "tenant_id"),
        Index("ix_canonical_order_tenant_ts", "tenant_id", "order_ts"),
        Index("ix_canonical_order_customer", "canonical_customer_id"),
    )

    canonical_order_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_customer_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    order_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    currency_base: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    amount_base: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=4), nullable=False)
    amount_src: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=4), nullable=False)
    currency_src: Mapped[str] = mapped_column(String(3), nullable=False)
    fx_rate: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=8), nullable=False)
    region: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    record_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# canonical_order_line  (mirrors 0001_canonical.canonical_order_line)
# ---------------------------------------------------------------------------
class CanonicalOrderLineRow(Base):
    """One line on a canonical order (FK back to the order, CASCADE delete)."""

    __tablename__ = "canonical_order_line"
    __table_args__ = (
        PrimaryKeyConstraint("canonical_order_line_id", name="pk_canonical_order_line"),
        UniqueConstraint("canonical_order_id", "line_no", name="uq_canonical_order_line_no"),
        Index("ix_canonical_order_line_tenant", "tenant_id"),
        Index("ix_canonical_order_line_order", "canonical_order_id"),
    )

    canonical_order_line_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, default=uuid4
    )
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_order_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_product_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_base: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=4), nullable=False)
    line_amount_base: Mapped[Decimal] = mapped_column(
        Numeric(precision=20, scale=4), nullable=False
    )


# ---------------------------------------------------------------------------
# ops_event  (mirrors 0001_canonical.ops_event)
# ---------------------------------------------------------------------------
class OpsEventRow(Base):
    """Immutable ops fact feeding error_rate / latency_p95 metrics."""

    __tablename__ = "ops_event"
    __table_args__ = (
        PrimaryKeyConstraint("canonical_ops_event_id", name="pk_ops_event"),
        CheckConstraint("level IN ('info','warn','error')", name="ck_ops_event_level"),
        Index("ix_ops_event_tenant", "tenant_id"),
        Index("ix_ops_event_tenant_service_ts", "tenant_id", "service", "event_ts"),
    )

    canonical_ops_event_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    service: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str | None] = mapped_column(Text, nullable=True)
    level: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    record_hash: Mapped[str] = mapped_column(Text, nullable=False)


# ---------------------------------------------------------------------------
# customer_activity  (mirrors 0001_canonical.customer_activity)
# ---------------------------------------------------------------------------
class CustomerActivityRow(Base):
    """Immutable customer-activity fact feeding page_views / sessions metrics."""

    __tablename__ = "customer_activity"
    __table_args__ = (
        PrimaryKeyConstraint("canonical_activity_id", name="pk_customer_activity"),
        CheckConstraint(
            "channel IS NULL OR channel IN ('web','partner','direct')",
            name="ck_customer_activity_channel",
        ),
        Index("ix_customer_activity_tenant", "tenant_id"),
        Index("ix_customer_activity_tenant_session", "tenant_id", "session_id"),
        Index("ix_customer_activity_tenant_ts", "tenant_id", "event_ts"),
    )

    canonical_activity_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_customer_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel: Mapped[str | None] = mapped_column(Text, nullable=True)
    props: Mapped[dict] = mapped_column(JSONB, nullable=False)
    event_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    record_hash: Mapped[str] = mapped_column(Text, nullable=False)


# ---------------------------------------------------------------------------
# metric_observations  (mirrors 0001_canonical.metric_observations)
# ---------------------------------------------------------------------------
class MetricObservationRow(Base):
    """A point in the metric series. Composite PK (tenant_id, metric_key, ts).

    The hypertable's PK must include the partition column ``ts`` -- so this is the
    natural key the upsert keys on. The optional ``embedding`` column (vector /
    jsonb) is intentionally **not** mapped: L2 never reads or writes it.
    """

    __tablename__ = "metric_observations"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "metric_key", "ts", name="pk_metric_observations"),
        Index("ix_metric_observations_tenant_key_ts", "tenant_id", "metric_key", "ts"),
    )

    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    metric_key: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dimensions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False)


# ---------------------------------------------------------------------------
# integration_outbox  (mirrors 0002_integration_outbox.integration_outbox)
# ---------------------------------------------------------------------------
class IntegrationOutboxRow(Base):
    """One event staged for publication inside the canonical-write transaction.

    The relay reads rows where ``published=false``, publishes ``payload`` to
    ``topic`` (under ``key``), and flips the flag -- so there is no
    persisted-but-not-published gap. ``event_id`` is unique (idempotent replay).
    """

    __tablename__ = "integration_outbox"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_integration_outbox"),
        UniqueConstraint("event_id", name="uq_integration_outbox_event_id"),
        Index("ix_integration_outbox_unpublished", "published", "created_at"),
        Index("ix_integration_outbox_tenant", "tenant_id"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# integration_idempotency  (mirrors 0002_integration_outbox.integration_idempotency)
# ---------------------------------------------------------------------------
class IntegrationIdempotencyRow(Base):
    """Processed-envelope idempotency keys (replay -> DUPLICATE, no re-write)."""

    __tablename__ = "integration_idempotency"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "idempotency_key", name="pk_integration_idempotency"),
    )

    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# integration_quarantine  (mirrors 0002_integration_outbox.integration_quarantine)
# ---------------------------------------------------------------------------
class IntegrationQuarantineRow(Base):
    """A persisted ``QuarantinedRecord`` (DQ failure / un-mappable record)."""

    __tablename__ = "integration_quarantine"
    __table_args__ = (
        PrimaryKeyConstraint("quarantine_id", name="pk_integration_quarantine"),
        Index("ix_integration_quarantine_tenant_ts", "tenant_id", "occurred_at"),
    )

    quarantine_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False, default="integration")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    dq_failures: Mapped[list] = mapped_column(JSONB, nullable=False)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reprocessed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


__all__ = [
    "CanonicalCustomerRow",
    "CanonicalProductRow",
    "CanonicalOrderRow",
    "CanonicalOrderLineRow",
    "OpsEventRow",
    "CustomerActivityRow",
    "MetricObservationRow",
    "IntegrationOutboxRow",
    "IntegrationIdempotencyRow",
    "IntegrationQuarantineRow",
]
