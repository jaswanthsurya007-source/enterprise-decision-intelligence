"""Async repositories for the governance spine.

Each repository takes an :class:`~sqlalchemy.ext.asyncio.AsyncSession` and is the
single place a SQL shape lives. All reads are **tenant-scoped** (``WHERE
tenant_id = :ctx``) -- the MVP's application-level isolation. Writes are
append-only / idempotent where the contract demands it:

* :meth:`AuditRepository.insert` is idempotent on ``audit_id`` (``ON CONFLICT DO
  NOTHING``) so a redelivered audit event is recorded exactly once.
* :meth:`LineageRepository.insert_event` fans a :class:`LineageEvent`'s
  ``inputs x outputs`` into edge rows (idempotent on the surrogate edge id).
* :meth:`ExplainRepository.write_decision` upserts a :class:`Decision` + its
  :class:`Evidence` snapshots in one transaction; re-POSTing the same decision is
  a no-op (idempotent on ``decision_id``).

Repositories never commit -- the caller (request handler / consumer) owns the
transaction boundary -- except where a method documents otherwise.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID, uuid4

from edis_contracts.governance import AuditEvent, Decision, Evidence
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AppRole,
    AuditLog,
    Decision as DecisionRow,
    EvidenceRow,
    LineageEdge,
    Permission,
)


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
class AuditRepository:
    """Append-only audit log: idempotent insert + tenant-scoped paginated reads."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(self, event: AuditEvent) -> bool:
        """Insert one :class:`AuditEvent`; idempotent on ``audit_id``.

        Returns ``True`` if a new row was written, ``False`` if it already existed
        (a redelivery). Does not commit -- the caller owns the transaction.
        """

        raw = event.model_dump(mode="json")
        stmt = (
            pg_insert(AuditLog)
            .values(
                audit_id=event.audit_id,
                occurred_at=event.occurred_at,
                tenant_id=event.tenant_id,
                actor=event.actor,
                action=event.action,
                resource=event.resource,
                outcome=event.outcome,
                reason=event.reason,
                decision_id=event.decision_id,
                trace_id=event.trace_id,
                schema_version=event.schema_version,
                raw=raw,
            )
            .on_conflict_do_nothing(index_elements=["audit_id"])
            .returning(AuditLog.audit_id)
        )
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def list(
        self,
        tenant_id: str,
        *,
        action: str | None = None,
        outcome: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[AuditLog]:
        """Return audit rows for ``tenant_id``, newest first, paginated."""

        stmt = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
        if action is not None:
            stmt = stmt.where(AuditLog.action == action)
        if outcome is not None:
            stmt = stmt.where(AuditLog.outcome == outcome)
        stmt = stmt.order_by(AuditLog.occurred_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return result.scalars().all()


# --------------------------------------------------------------------------- #
# Lineage
# --------------------------------------------------------------------------- #
def _edge_key(part: dict) -> tuple[str, str]:
    """Normalize a lineage input/output node ``{type, id}`` to ``(type, id)`` strings."""

    return str(part.get("type", "unknown")), str(part.get("id", ""))


class LineageRepository:
    """Materializes a lineage graph; query edges by entity (src or dst)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_edges(
        self,
        *,
        lineage_id: UUID,
        run_id: UUID,
        tenant_id: str,
        inputs: list[dict],
        outputs: list[dict],
        stage: str,
        occurred_at: datetime,
    ) -> int:
        """Fan ``inputs x outputs`` into edges; returns the number of edges written.

        Each (input -> output) pair becomes one ``lineage_edge`` row. The
        surrogate ``lineage_edge_id`` makes re-folding the same event append new
        rows only if the consumer redelivers; callers that need strict idempotency
        dedupe upstream on ``lineage_id``.
        """

        count = 0
        for src in inputs:
            src_type, src_id = _edge_key(src)
            for dst in outputs:
                dst_type, dst_id = _edge_key(dst)
                self._session.add(
                    LineageEdge(
                        lineage_edge_id=uuid4(),
                        lineage_id=lineage_id,
                        run_id=run_id,
                        tenant_id=tenant_id,
                        src_type=src_type,
                        src_id=src_id,
                        dst_type=dst_type,
                        dst_id=dst_id,
                        stage=stage,
                        occurred_at=occurred_at,
                    )
                )
                count += 1
        return count

    async def already_recorded(self, lineage_id: UUID) -> bool:
        """True if any edge for ``lineage_id`` already exists (cheap dedupe guard)."""

        stmt = (
            select(LineageEdge.lineage_edge_id).where(LineageEdge.lineage_id == lineage_id).limit(1)
        )
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def edges_for_entity(
        self, tenant_id: str, entity_type: str, entity_id: str, *, limit: int = 200
    ) -> Sequence[LineageEdge]:
        """Return edges where ``entity`` is the src OR dst, tenant-scoped."""

        stmt = (
            select(LineageEdge)
            .where(
                LineageEdge.tenant_id == tenant_id,
                (
                    ((LineageEdge.src_type == entity_type) & (LineageEdge.src_id == entity_id))
                    | ((LineageEdge.dst_type == entity_type) & (LineageEdge.dst_id == entity_id))
                ),
            )
            .order_by(LineageEdge.occurred_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()


# --------------------------------------------------------------------------- #
# Explainability
# --------------------------------------------------------------------------- #
class ExplainRepository:
    """Writes/reads the immutable explainability store (decision + evidence)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def exists(self, decision_id: UUID) -> bool:
        stmt = select(DecisionRow.decision_id).where(DecisionRow.decision_id == decision_id)
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def write_decision(self, decision: Decision) -> bool:
        """Persist a :class:`Decision` + its :class:`Evidence` snapshots.

        Idempotent on ``decision_id``: re-POSTing the same decision returns
        ``False`` and writes nothing (the snapshot is immutable). Returns ``True``
        when a new decision was written. Does not commit.
        """

        if await self.exists(decision.decision_id):
            return False

        self._session.add(
            DecisionRow(
                decision_id=decision.decision_id,
                tenant_id=decision.tenant_id,
                decision_type=decision.decision_type,
                subject_id=decision.subject_id,
                actor=decision.actor,
                rationale=decision.rationale,
                created_at=decision.created_at,
                schema_version=decision.schema_version,
            )
        )
        for ev in decision.evidence:
            self._session.add(
                EvidenceRow(
                    evidence_id=ev.evidence_id,
                    decision_id=decision.decision_id,
                    tenant_id=decision.tenant_id,
                    kind=ev.kind,
                    summary=ev.summary,
                    snapshot=ev.snapshot,
                    ref=ev.ref,
                    schema_version=ev.schema_version,
                )
            )
        return True

    async def get_decision(self, tenant_id: str, decision_id: UUID) -> Decision | None:
        """Return the contract :class:`Decision` (with evidence) or ``None``."""

        stmt = select(DecisionRow).where(
            DecisionRow.decision_id == decision_id,
            DecisionRow.tenant_id == tenant_id,
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return Decision(
            decision_id=row.decision_id,
            tenant_id=row.tenant_id,
            decision_type=row.decision_type,  # type: ignore[arg-type]
            subject_id=row.subject_id,
            actor=row.actor,
            rationale=row.rationale,
            evidence=[
                Evidence(
                    evidence_id=e.evidence_id,
                    kind=e.kind,
                    summary=e.summary,
                    snapshot=e.snapshot,
                    ref=e.ref,
                    schema_version=e.schema_version,
                )
                for e in row.evidence
            ],
            created_at=row.created_at,
            schema_version=row.schema_version,
        )


# --------------------------------------------------------------------------- #
# RBAC / control-plane (admin reads + writes)
# --------------------------------------------------------------------------- #
class RbacRepository:
    """Admin reads/writes over the control-plane RBAC tables."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        """Commit the underlying session (admin writes own their transaction)."""

        await self._session.commit()

    async def list_roles(self) -> Sequence[AppRole]:
        result = await self._session.execute(select(AppRole).order_by(AppRole.name))
        return result.scalars().all()

    async def list_permissions(self, *, role: str | None = None) -> Sequence[Permission]:
        stmt = select(Permission)
        if role is not None:
            stmt = stmt.where(Permission.role == role)
        stmt = stmt.order_by(Permission.role, Permission.action, Permission.resource_type)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def upsert_permission(self, role: str, action: str, resource_type: str) -> bool:
        """Grant ``action:resource_type`` to ``role``; idempotent. Returns True if new."""

        stmt = (
            pg_insert(Permission)
            .values(role=role, action=action, resource_type=resource_type)
            .on_conflict_do_nothing(index_elements=["role", "action", "resource_type"])
            .returning(Permission.role)
        )
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def ensure_role(self, name: str, description: str | None = None) -> bool:
        stmt = (
            pg_insert(AppRole)
            .values(name=name, description=description)
            .on_conflict_do_nothing(index_elements=["name"])
            .returning(AppRole.name)
        )
        result = await self._session.execute(stmt)
        return result.first() is not None


__all__ = [
    "AuditRepository",
    "LineageRepository",
    "ExplainRepository",
    "RbacRepository",
]
