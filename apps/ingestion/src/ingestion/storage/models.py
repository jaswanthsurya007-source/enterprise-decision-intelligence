"""SQLAlchemy ORM models for the ingestion landing store.

All models hang off the shared :class:`edis_platform.db.session.Base` so every
service's tables share one declarative registry. Every row carries ``tenant_id``
(multi-tenant partition + app-level filtering). Importing this module opens no
connection.

Tables:

* ``raw_events``      — the outbox / durable landing record. ``idempotency_key``
  is **unique** (DB-level dedupe backstop behind the Redis guard); ``published``
  tracks outbox state for the reconcile relay.
* ``ingest_dlq``      — persisted dead-letter records (full error context).
* ``ingest_checkpoint`` — per-source byte/row offset for the chunked batch loader.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from edis_platform.db.session import Base
from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column


class RawEvent(Base):
    """One landed source record — the durable, replayable outbox row."""

    __tablename__ = "raw_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_raw_events_idempotency_key"),
        Index("ix_raw_events_tenant_domain_ts", "tenant_id", "domain", "event_ts"),
        Index("ix_raw_events_unpublished", "published"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    domain: Mapped[str] = mapped_column(String(32), nullable=False)
    source_system: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    event_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    anomaly_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ingest_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class IngestDLQ(Base):
    """A persisted dead-letter record (parallels the published ``DLQRecord``)."""

    __tablename__ = "ingest_dlq"
    __table_args__ = (Index("ix_ingest_dlq_tenant_ts", "tenant_id", "occurred_at"),)

    dlq_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="ingest")
    domain: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_system: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_type: Mapped[str] = mapped_column(String(128), nullable=False)
    error_detail: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    replayed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class IngestCheckpoint(Base):
    """Per-source checkpoint for the chunked, resumable batch loader."""

    __tablename__ = "ingest_checkpoint"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_key", name="uq_ingest_checkpoint_source"),
    )

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    #: Stable identifier of the source being loaded (e.g. a file path / URI).
    source_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    #: Last fully-processed offset (row index for CSV/JSONL, row group for parquet).
    offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_ingested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
