"""Async SQLAlchemy repository for recommendations, lifecycle, and outcomes.

This is the typed access layer the C2 lifecycle manager, REST API, finding consumer,
and no-op outcome recorder write through. Every method is **tenant-scoped** (the
``tenant_id`` is always carried, MVP isolation is application-level filtering -- no RLS
FORCE yet) and works against the ``recommendation`` / ``recommendation_lifecycle`` /
``outcome_report`` tables owned by ``alembic/versions/0001_decision.py`` and mirrored by
:mod:`decision_engine.models`.

The repository converts between the canonical Pydantic contracts
(:class:`~edis_contracts.decisions.Recommendation`,
:class:`~edis_contracts.decisions.RecommendationLifecycleEvent`,
:class:`~edis_contracts.decisions.OutcomeReport`) and their ORM rows, so callers stay in
contract-land. Reads are ``ORDER BY priority_rank`` (ascending -- rank 1 first) and
paginated by ``limit``/``offset``.

Importing this module opens no connection. Construct with an
:class:`~sqlalchemy.ext.asyncio.AsyncSession`; the caller owns the session/transaction
lifecycle (the FastAPI session dependency commits/rolls back, the consumers commit
explicitly).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from edis_contracts.decisions import (
    ConfidenceScore,
    ImpactEstimate,
    OutcomeReport,
    Recommendation,
    RecommendationLifecycleEvent,
)
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from decision_engine.models.recommendation import (
    OutcomeReportRow,
    RecommendationLifecycleRow,
    RecommendationRow,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_recommendation(row: RecommendationRow) -> Recommendation:
    """Rehydrate a :class:`Recommendation` contract from its ORM row."""

    return Recommendation(
        recommendation_id=row.recommendation_id,
        tenant_id=row.tenant_id,
        source_finding_id=row.source_finding_id,
        playbook_id=row.playbook_id,
        playbook_version=row.playbook_version,
        title=row.title,
        action_type=row.action_type,  # type: ignore[arg-type]  # validated by pydantic
        action_params=dict(row.action_params or {}),
        impact=ImpactEstimate.model_validate(row.impact),
        effort_tier=row.effort_tier,  # type: ignore[arg-type]
        confidence=ConfidenceScore.model_validate(row.confidence),
        priority_score=row.priority_score,
        priority_rank=row.priority_rank,
        explanation_summary=row.explanation_summary,
        evidence_trail=list(row.evidence_trail or []),
        narrative=row.narrative,
        status=row.status,  # type: ignore[arg-type]
        expires_at=row.expires_at,
        created_at=row.created_at,
        schema_version=row.schema_version,
    )


class RecommendationRepository:
    """Tenant-scoped async CRUD for L4 recommendations + lifecycle + outcomes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # recommendation
    # ------------------------------------------------------------------
    async def save_recommendation(self, rec: Recommendation) -> None:
        """Upsert one :class:`Recommendation` (idempotent on ``recommendation_id``).

        Used by the finding consumer before publish so there is no
        published-but-not-stored gap. On conflict the row is overwritten (a replayed
        finding re-synthesizes the same deterministic recommendation).
        """

        values = {
            "recommendation_id": rec.recommendation_id,
            "tenant_id": rec.tenant_id,
            "source_finding_id": rec.source_finding_id,
            "playbook_id": rec.playbook_id,
            "playbook_version": rec.playbook_version,
            "title": rec.title,
            "action_type": rec.action_type,
            "action_params": rec.action_params,
            "impact": rec.impact.model_dump(),
            "effort_tier": rec.effort_tier,
            "confidence": rec.confidence.model_dump(),
            "priority_score": rec.priority_score,
            "priority_rank": rec.priority_rank,
            "explanation_summary": rec.explanation_summary,
            "evidence_trail": rec.evidence_trail,
            "narrative": rec.narrative,
            "status": rec.status,
            "expires_at": rec.expires_at,
            "created_at": rec.created_at,
            "schema_version": rec.schema_version,
        }
        stmt = pg_insert(RecommendationRow).values(**values)
        update_cols = {k: stmt.excluded[k] for k in values if k != "recommendation_id"}
        stmt = stmt.on_conflict_do_update(
            index_elements=[RecommendationRow.recommendation_id], set_=update_cols
        )
        await self._session.execute(stmt)

    async def get(self, tenant_id: str, recommendation_id: UUID) -> Recommendation | None:
        """Fetch one tenant-scoped recommendation by id, or ``None``."""

        row = await self._get_row(tenant_id, recommendation_id)
        return _row_to_recommendation(row) if row is not None else None

    async def _get_row(self, tenant_id: str, recommendation_id: UUID) -> RecommendationRow | None:
        stmt = select(RecommendationRow).where(
            RecommendationRow.tenant_id == tenant_id,
            RecommendationRow.recommendation_id == recommendation_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_tenant(
        self,
        tenant_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Recommendation]:
        """List a tenant's recommendations, sorted by ``priority_rank`` (rank 1 first).

        Optionally filtered by ``status``; paginated by ``limit``/``offset``. Ties on
        ``priority_rank`` are broken by ``created_at`` descending so the order is stable.
        """

        stmt = select(RecommendationRow).where(RecommendationRow.tenant_id == tenant_id)
        if status is not None:
            stmt = stmt.where(RecommendationRow.status == status)
        stmt = (
            stmt.order_by(
                RecommendationRow.priority_rank.asc(),
                RecommendationRow.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [_row_to_recommendation(r) for r in result.scalars().all()]

    async def count_for_tenant(self, tenant_id: str, *, status: str | None = None) -> int:
        """Count a tenant's recommendations (for pagination metadata)."""

        stmt = (
            select(func.count())
            .select_from(RecommendationRow)
            .where(RecommendationRow.tenant_id == tenant_id)
        )
        if status is not None:
            stmt = stmt.where(RecommendationRow.status == status)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def update_status(
        self, tenant_id: str, recommendation_id: UUID, to_status: str
    ) -> str | None:
        """Set ``status`` on a tenant-scoped recommendation; return the PREVIOUS status.

        Returns ``None`` if no such recommendation exists (the caller raises 404). The
        returned ``from_status`` is what the lifecycle manager records on the transition
        event. The status validity of the transition is enforced upstream by the FSM;
        this method just persists the new value.
        """

        row = await self._get_row(tenant_id, recommendation_id)
        if row is None:
            return None
        previous = row.status
        row.status = to_status
        await self._session.flush()
        return previous

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def record_lifecycle(self, event: RecommendationLifecycleEvent) -> None:
        """Append one lifecycle transition row (idempotent on ``event_id``)."""

        stmt = (
            pg_insert(RecommendationLifecycleRow)
            .values(
                event_id=event.event_id,
                tenant_id=event.tenant_id,
                recommendation_id=event.recommendation_id,
                from_status=event.from_status,
                to_status=event.to_status,
                actor=event.actor,
                occurred_at=event.occurred_at,
                schema_version=event.schema_version,
            )
            .on_conflict_do_nothing(index_elements=[RecommendationLifecycleRow.event_id])
        )
        await self._session.execute(stmt)

    async def list_expired_candidates(
        self, *, now: datetime | None = None, limit: int = 500
    ) -> list[Recommendation]:
        """Return ``proposed`` recommendations whose ``expires_at`` is in the past.

        The TTL sweeper iterates these and expires them. Not tenant-scoped (the sweeper
        runs across all tenants) but each returned recommendation carries its own
        ``tenant_id`` so downstream events/audit stay tenant-correct.
        """

        now = now or _utc_now()
        stmt = (
            select(RecommendationRow)
            .where(
                RecommendationRow.status == "proposed",
                RecommendationRow.expires_at < now,
            )
            .order_by(RecommendationRow.expires_at.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [_row_to_recommendation(r) for r in result.scalars().all()]

    # ------------------------------------------------------------------
    # outcomes (no-op recorder target -- persisted, nothing computed)
    # ------------------------------------------------------------------
    async def save_outcome(self, outcome: OutcomeReport) -> None:
        """Persist one :class:`OutcomeReport` (idempotent on ``outcome_id``).

        This is the entire job of the feedback seam in the MVP: the row lands, and
        NOTHING is computed over it (no realized-vs-predicted error, no recalibration --
        those are the deferred feedback loop). The seam is demonstrably wired.
        """

        stmt = (
            pg_insert(OutcomeReportRow)
            .values(
                outcome_id=outcome.outcome_id,
                tenant_id=outcome.tenant_id,
                recommendation_id=outcome.recommendation_id,
                source=outcome.source,
                accepted=outcome.accepted,
                realized_value=outcome.realized_value,
                realized_unit=outcome.realized_unit,
                notes=outcome.notes,
                occurred_at=outcome.occurred_at,
                schema_version=outcome.schema_version,
            )
            .on_conflict_do_nothing(index_elements=[OutcomeReportRow.outcome_id])
        )
        await self._session.execute(stmt)
