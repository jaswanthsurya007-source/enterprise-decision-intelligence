"""SQLAlchemy ORM models for the governance spine.

These mirror the D1 Alembic DDL (``app/migrations/versions/0001_governance.py``)
**exactly** -- same table names, columns, types, constraints, and indexes -- so
the ORM reads/writes the migrated schema without surprises. Every table carries
``tenant_id`` (MVP isolation is application-level filtering; no RLS FORCE).

Notes that keep ORM and DDL in lockstep:

* ``audit_log`` is a TimescaleDB hypertable on ``occurred_at`` in production, so
  its real uniqueness is ``(audit_id, occurred_at)``; on a plain Postgres the PK
  is ``audit_id`` alone. The ORM declares ``audit_id`` as the (logical) primary
  key -- the column the consumer dedupes on with ``ON CONFLICT DO NOTHING`` --
  and includes ``occurred_at`` so an ORM-driven create_all in tests still yields
  a valid composite-aware table.
* ``decision.embedding`` is ``vector(1024)`` under pgvector and JSONB otherwise;
  the ORM never selects it (explainability reads/writes go through the JSON
  columns), so it is intentionally **not** mapped.

The shared :class:`edis_platform.db.session.Base` is reused so a service that
imports several models keeps one metadata/registry.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from edis_platform.db.session import Base
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class Tenant(Base):
    """Control-plane ``tenant(id, name)`` registry."""

    __tablename__ = "tenant"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AppRole(Base):
    """Control-plane RBAC role registry (viewer/analyst/operator/auditor/admin)."""

    __tablename__ = "app_role"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class Permission(Base):
    """Control-plane ``(role, action, resource_type)`` rows.

    Mirrors :data:`edis_platform.authz.rbac.ROLE_PERMISSIONS`. The runtime RBAC
    check is the pure ``evaluate()`` function; this table is the persisted, admin-
    queryable projection of the same matrix.
    """

    __tablename__ = "permission"
    __table_args__ = (Index("ix_permission_role", "role"),)

    role: Mapped[str] = mapped_column(
        Text, ForeignKey("app_role.name", ondelete="CASCADE"), primary_key=True
    )
    action: Mapped[str] = mapped_column(Text, primary_key=True)
    resource_type: Mapped[str] = mapped_column(Text, primary_key=True)


class CalibrationPrior(Base):
    """Pre-seeded static per-(tenant, playbook) calibration prior (``n=0`` in MVP)."""

    __tablename__ = "calibration_prior"

    tenant_id: Mapped[str] = mapped_column(Text, ForeignKey("tenant.id"), primary_key=True)
    playbook_id: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Decision(Base):
    """Explainability record (the :class:`edis_contracts.governance.Decision` shape)."""

    __tablename__ = "decision"
    __table_args__ = (
        CheckConstraint(
            "decision_type IN ('finding_narrative','recommendation','copilot_answer')",
            name="ck_decision_type",
        ),
        Index("ix_decision_tenant", "tenant_id"),
        Index("ix_decision_tenant_subject", "tenant_id", "subject_id"),
    )

    decision_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    decision_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    evidence: Mapped[list["EvidenceRow"]] = relationship(
        "EvidenceRow",
        back_populates="decision",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class EvidenceRow(Base):
    """Immutable evidence snapshot (the :class:`edis_contracts.governance.Evidence` shape)."""

    __tablename__ = "evidence"
    __table_args__ = (
        Index("ix_evidence_decision", "decision_id"),
        Index("ix_evidence_tenant", "tenant_id"),
    )

    evidence_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    decision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("decision.decision_id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    decision: Mapped["Decision"] = relationship("Decision", back_populates="evidence")


class LineageEdge(Base):
    """Materialized lineage edge (one src->dst pair fanned out from a LineageEvent)."""

    __tablename__ = "lineage_edge"
    __table_args__ = (
        Index("ix_lineage_edge_tenant", "tenant_id"),
        Index("ix_lineage_edge_run", "run_id"),
        Index("ix_lineage_edge_src", "tenant_id", "src_type", "src_id"),
        Index("ix_lineage_edge_dst", "tenant_id", "dst_type", "dst_id"),
    )

    lineage_edge_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    lineage_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    src_type: Mapped[str] = mapped_column(Text, nullable=False)
    src_id: Mapped[str] = mapped_column(Text, nullable=False)
    dst_type: Mapped[str] = mapped_column(Text, nullable=False)
    dst_id: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditLog(Base):
    """Append-only audit row (the :class:`edis_contracts.governance.AuditEvent` shape).

    The migrated table is a TimescaleDB hypertable on ``occurred_at``; the ORM
    treats ``audit_id`` as the logical idempotency key (the consumer inserts with
    ``ON CONFLICT (audit_id) DO NOTHING``). ``raw`` holds a verbatim jsonb copy of
    the inbound event.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        UniqueConstraint("audit_id", "occurred_at", name="uq_audit_log_audit_id_time"),
        CheckConstraint(
            "action IN ('DATA_READ','DATA_WRITE','AI_DECISION','AI_QUERY',"
            "'AUTH_DENY','RBAC_CHANGE','EXPORT')",
            name="ck_audit_log_action",
        ),
        CheckConstraint("outcome IN ('ALLOW','DENY','ERROR')", name="ck_audit_log_outcome"),
        Index("ix_audit_log_tenant", "tenant_id"),
        Index("ix_audit_log_tenant_time", "tenant_id", "occurred_at"),
        Index("ix_audit_log_action", "tenant_id", "action"),
        Index("ix_audit_log_decision", "decision_id"),
    )

    audit_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[dict] = mapped_column(JSONB, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource: Mapped[dict] = mapped_column(JSONB, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


__all__ = [
    "Tenant",
    "AppRole",
    "Permission",
    "CalibrationPrior",
    "Decision",
    "EvidenceRow",
    "LineageEdge",
    "AuditLog",
]
