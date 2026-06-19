"""SQLAlchemy ORM for the L3 store — mirrors ``0001_intelligence`` exactly.

Every model hangs off the shared :class:`edis_platform.db.session.Base` (one
declarative registry across services) and **every column mirrors the hand-authored
DDL in ``migrations/versions/0001_intelligence.py``**: same table names, columns,
types, nullability, primary keys, FK, and check constraints. The migration owns the
schema (it runs under ``@pytest.mark.integration``); this ORM is the typed access
layer the async repositories write through. Importing this module opens no connection.

The pgvector ``embedding`` column is intentionally **not mapped as a typed column**
here. The migration adds it as ``vector(1024)`` when pgvector is present and as
``jsonb`` otherwise, and the optional ``pgvector`` Python package may not be
installed — so the ORM stays agnostic. The repository writes/reads ``embedding`` via
explicit SQL (a JSON-encoded list, which pgvector accepts as a vector literal and
plain Postgres stores as jsonb), keeping this model import-safe everywhere.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from edis_platform.db.session import Base
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column


# ---------------------------------------------------------------------------
# evidence_bundle  (mirrors 0001_intelligence.evidence_bundle)
# ---------------------------------------------------------------------------
class EvidenceBundleRow(Base):
    """The EvidenceBundle the narrator reasoned over (FK target of findings.evidence_ref)."""

    __tablename__ = "evidence_bundle"
    __table_args__ = (
        PrimaryKeyConstraint("bundle_id", name="pk_evidence_bundle"),
        Index("ix_evidence_bundle_tenant", "tenant_id"),
        Index("ix_evidence_bundle_finding", "finding_id"),
    )

    bundle_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    finding_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # EvidenceItem[] (kind/metric_key/dimensions/summary/values/ref).
    items: Mapped[list] = mapped_column(JSONB, nullable=False)
    # allowed_numbers whitelist the grounding guard enforces.
    allowed_numbers: Mapped[list] = mapped_column(JSONB, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


# ---------------------------------------------------------------------------
# findings  (mirrors 0001_intelligence.findings)
# ---------------------------------------------------------------------------
class FindingRow(Base):
    """One persisted ``edis.findings.v1`` Finding (computed detection + grounded narrative).

    ``embedding`` is added by the migration (``vector(1024)`` or ``jsonb``) and is NOT
    mapped here — the repository reads/writes it via explicit SQL.
    """

    __tablename__ = "findings"
    __table_args__ = (
        PrimaryKeyConstraint("finding_id", name="pk_findings"),
        ForeignKeyConstraint(
            ["evidence_ref"],
            ["evidence_bundle.bundle_id"],
            name="fk_findings_evidence",
            ondelete="SET NULL",
        ),
        CheckConstraint(
            "kind IN ('point_anomaly','seasonal_break','level_shift',"
            "'trend_break','forecast_breach','root_cause')",
            name="ck_findings_kind",
        ),
        CheckConstraint(
            "status IN ('open','acknowledged','resolved','expired')",
            name="ck_findings_status",
        ),
        Index("ix_findings_tenant", "tenant_id"),
        Index("ix_findings_tenant_metric_window", "tenant_id", "metric_key", "window_start"),
        Index("ix_findings_tenant_status", "tenant_id", "status"),
        Index("ix_findings_tenant_created", "tenant_id", "created_at"),
    )

    finding_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    metric_key: Mapped[str] = mapped_column(Text, nullable=False)
    dimensions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detector: Mapped[str] = mapped_column(Text, nullable=False)
    detector_version: Mapped[str] = mapped_column(Text, nullable=False)
    observed_value: Mapped[float] = mapped_column(Float, nullable=False)
    expected_value: Mapped[float] = mapped_column(Float, nullable=False)
    deviation: Mapped[float] = mapped_column(Float, nullable=False)
    deviation_pct: Mapped[float] = mapped_column(Float, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    business_impact_input: Mapped[float] = mapped_column(Float, nullable=False)
    candidate_causes: Mapped[list] = mapped_column(JSONB, nullable=False)
    narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    narrative_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_ref: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


# ---------------------------------------------------------------------------
# forecasts  (mirrors 0001_intelligence.forecasts)
# ---------------------------------------------------------------------------
class ForecastRow(Base):
    """One persisted ``edis.forecasts.v1`` Forecast (the single ETS band)."""

    __tablename__ = "forecasts"
    __table_args__ = (
        PrimaryKeyConstraint("forecast_id", name="pk_forecasts"),
        Index("ix_forecasts_tenant", "tenant_id"),
        Index(
            "ix_forecasts_tenant_metric_generated",
            "tenant_id",
            "metric_key",
            "generated_at",
        ),
    )

    forecast_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    metric_key: Mapped[str] = mapped_column(Text, nullable=False)
    dimensions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    points: Mapped[list] = mapped_column(JSONB, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
