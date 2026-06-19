"""The lifecycle manager: validate -> persist -> publish -> audit, plus the TTL sweeper.

:class:`LifecycleManager` is the one place a recommendation's status changes. For every
transition it:

1. **validates** the move against the pure :class:`LifecycleStateMachine` -- an illegal
   transition raises :class:`~edis_platform.errors.ConflictError` (HTTP 409) BEFORE any
   side effect, so a rejected transition never half-applies;
2. **persists** the new status on the recommendation and appends a
   ``recommendation_lifecycle`` row (one transaction, owned by the caller's session);
3. **publishes** the :class:`~edis_contracts.decisions.RecommendationLifecycleEvent` on
   ``edis.decisions.lifecycle.v1`` (keyed by ``recommendation_id``);
4. **emits a governance audit event** (``AI_DECISION``) via the governance SDK.

Two entry points:

* :meth:`transition` (operator-driven: accept / reject from the REST API), which loads the
  current status, gates it, and runs the four steps with the operator as the actor;
* :meth:`sweep_expired` (the TTL sweeper), which finds stale ``proposed`` recommendations
  past ``expires_at`` and expires each with a ``system`` actor.

The manager never calls an LLM and never invents a number. It is constructed with the
collaborators (repository, event producer, audit sink) so tests inject infra-free fakes.
The repository/session transaction is committed by the caller (the API session dependency
or the sweeper loop) AFTER :meth:`transition` returns, so persist + publish stay together.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from edis_contracts.decisions import Recommendation, RecommendationLifecycleEvent
from edis_contracts.security import SecurityContext
from edis_platform.errors import NotFoundError
from edis_platform.logging import get_logger
from edis_gov_sdk.audit import emit_audit

from decision_engine.events.producer import DecisionEventProducer
from decision_engine.lifecycle.state_machine import LifecycleStateMachine
from decision_engine.persistence.repository import RecommendationRepository

_log = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _actor_from_ctx(ctx: SecurityContext | None) -> dict:
    """Build the lifecycle ``actor`` payload ``{type, id}`` from a security context."""

    if ctx is None:
        return {"type": "system", "id": "decision-engine"}
    return {"type": "user", "id": ctx.user_id, "roles": list(ctx.roles)}


class LifecycleManager:
    """Validates + applies recommendation status transitions, with events + audit."""

    def __init__(
        self,
        repo: RecommendationRepository,
        producer: DecisionEventProducer,
        audit_sink,
        *,
        fsm: LifecycleStateMachine | None = None,
    ) -> None:
        self._repo = repo
        self._producer = producer
        self._audit_sink = audit_sink
        self._fsm = fsm or LifecycleStateMachine()

    async def transition(
        self,
        tenant_id: str,
        recommendation_id: UUID,
        to_status: str,
        *,
        ctx: SecurityContext | None = None,
    ) -> Recommendation:
        """Move a recommendation to ``to_status`` (validated); return the updated record.

        Raises :class:`NotFoundError` (404) if no such tenant-scoped recommendation
        exists, or :class:`~edis_platform.errors.ConflictError` (409) if the transition
        is illegal from its current status. On success, persists the new status, appends
        a lifecycle row, publishes the lifecycle event, and emits an audit event.
        """

        current = await self._repo.get(tenant_id, recommendation_id)
        if current is None:
            raise NotFoundError(f"Recommendation '{recommendation_id}' not found.")

        # Gate BEFORE any side effect (raises 409 on an illegal move).
        self._fsm.validate(current.status, to_status)

        # Persist the new status (also re-fetches the previous, defensively).
        from_status = await self._repo.update_status(tenant_id, recommendation_id, to_status)
        from_status = from_status if from_status is not None else current.status

        await self._emit_transition(
            tenant_id=tenant_id,
            recommendation_id=recommendation_id,
            from_status=from_status,
            to_status=to_status,
            ctx=ctx,
        )

        updated = current.model_copy(update={"status": to_status})
        _log.info(
            "recommendation transitioned",
            extra={
                "tenant_id": tenant_id,
                "recommendation_id": str(recommendation_id),
                "from_status": from_status,
                "to_status": to_status,
                "actor": _actor_from_ctx(ctx).get("id"),
            },
        )
        return updated

    async def sweep_expired(self, *, now: datetime | None = None, limit: int = 500) -> int:
        """Expire stale ``proposed`` recommendations past ``expires_at``.

        Returns the number expired. Each is moved ``proposed -> expired`` with a
        ``system`` actor, emitting the lifecycle + audit events. One bad record never
        kills the sweep -- failures are logged and the loop continues. The caller commits
        the session after the sweep.
        """

        now = now or _utc_now()
        candidates = await self._repo.list_expired_candidates(now=now, limit=limit)
        expired = 0
        for rec in candidates:
            # Re-validate defensively (a concurrent accept could have moved it).
            if not self._fsm.can_transition(rec.status, "expired"):
                continue
            try:
                from_status = await self._repo.update_status(
                    rec.tenant_id, rec.recommendation_id, "expired"
                )
                from_status = from_status if from_status is not None else rec.status
                await self._emit_transition(
                    tenant_id=rec.tenant_id,
                    recommendation_id=rec.recommendation_id,
                    from_status=from_status,
                    to_status="expired",
                    ctx=None,
                )
                expired += 1
            except Exception as exc:  # noqa: BLE001 - one bad record must not kill the sweep
                _log.warning(
                    "ttl sweep failed to expire recommendation",
                    extra={
                        "tenant_id": rec.tenant_id,
                        "recommendation_id": str(rec.recommendation_id),
                        "error": str(exc),
                    },
                )
        if expired:
            _log.info("ttl sweep expired recommendations", extra={"count": expired})
        return expired

    async def _emit_transition(
        self,
        *,
        tenant_id: str,
        recommendation_id: UUID,
        from_status: str,
        to_status: str,
        ctx: SecurityContext | None,
    ) -> RecommendationLifecycleEvent:
        """Append the lifecycle row, publish the event, and emit the audit event."""

        actor = _actor_from_ctx(ctx)
        event = RecommendationLifecycleEvent(
            event_id=uuid4(),
            tenant_id=tenant_id,
            recommendation_id=recommendation_id,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            occurred_at=_utc_now(),
        )
        # Persist the transition row (same session/txn as the status update).
        await self._repo.record_lifecycle(event)
        # Publish on edis.decisions.lifecycle.v1 (keyed by recommendation_id).
        await self._producer.publish_lifecycle(event)
        # Governance: audit the AI decision lifecycle change.
        await emit_audit(
            self._audit_sink,
            ctx,
            "AI_DECISION",
            {"type": "recommendation", "id": str(recommendation_id)},
            "ALLOW",
            tenant_id=tenant_id,
            reason=f"lifecycle {from_status} -> {to_status}",
        )
        return event
