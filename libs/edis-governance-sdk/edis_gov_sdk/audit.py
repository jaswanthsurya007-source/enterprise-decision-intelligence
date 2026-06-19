"""Audit emission -- every layer's one-liner to record an :class:`AuditEvent`.

The append-only audit log is the governance spine: every data access and every
AI decision emits an :class:`~edis_contracts.governance.AuditEvent` to the
``edis.governance.audit.v1`` topic, which the governance service consumes into the
(idempotent on ``audit_id``) ``audit_log`` hypertable.

:class:`AuditEmitter` owns no database -- it publishes through an injected
:class:`~edis_platform.bus.base.EventSink`, so the same call works over Kafka,
Redis Streams, or the in-process bus used in tests. The ``actor`` /
``tenant_id`` are derived from the verified
:class:`~edis_contracts.security.SecurityContext` when one is supplied (never from
request bodies), with explicit overrides for system/background producers that
have no principal.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from edis_contracts import topics
from edis_contracts.governance import AuditEvent
from edis_contracts.security import SecurityContext


def _utc_now() -> datetime:
    """Timezone-aware UTC now (every EDIS timestamp is tz-aware UTC)."""

    return datetime.now(timezone.utc)


def _actor_from_ctx(ctx: SecurityContext | None) -> dict:
    """Build the audit ``actor`` payload ``{type, id, roles}`` from a context."""

    if ctx is None:
        return {"type": "system", "id": "system", "roles": []}
    return {"type": "user", "id": ctx.user_id, "roles": list(ctx.roles)}


def build_audit_event(
    ctx: SecurityContext | None,
    action: str,
    resource: dict,
    outcome: str,
    *,
    reason: str | None = None,
    decision_id: UUID | None = None,
    trace_id: str | None = None,
    tenant_id: str | None = None,
) -> AuditEvent:
    """Construct an :class:`AuditEvent` with a fresh ``audit_id`` and UTC ``occurred_at``.

    ``tenant_id`` falls back to ``ctx.tenant_id`` when not given explicitly. The
    returned model is validated against the contract (so an unknown ``action`` /
    ``outcome`` fails fast at the call site, not in the audit consumer).
    """

    resolved_tenant = tenant_id if tenant_id is not None else (ctx.tenant_id if ctx else None)
    if resolved_tenant is None:
        raise ValueError("tenant_id is required (pass it explicitly or supply a SecurityContext)")

    return AuditEvent(
        audit_id=uuid4(),
        occurred_at=_utc_now(),
        tenant_id=resolved_tenant,
        actor=_actor_from_ctx(ctx),
        action=action,  # type: ignore[arg-type]  # Literal validated by pydantic
        resource=resource,
        outcome=outcome,  # type: ignore[arg-type]
        reason=reason,
        decision_id=decision_id,
        trace_id=trace_id,
    )


class AuditEmitter:
    """Publishes :class:`AuditEvent`\\ s to ``edis.governance.audit.v1`` via a sink.

    Inject the process-wide :class:`~edis_platform.bus.base.EventSink`; the
    emitter does not start/stop it (the owning service manages the sink
    lifecycle). One emitter is cheap and safe to share across requests.
    """

    def __init__(self, sink) -> None:
        self._sink = sink

    async def emit(
        self,
        ctx: SecurityContext | None,
        action: str,
        resource: dict,
        outcome: str,
        reason: str | None = None,
        decision_id: UUID | None = None,
        trace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> AuditEvent:
        """Build and publish an :class:`AuditEvent`, returning the built event.

        The event is keyed by ``tenant_id`` on the bus (per the topic contract),
        preserving per-tenant ordering. The built event is returned so callers
        can correlate (e.g. log the ``audit_id``).
        """

        event = build_audit_event(
            ctx,
            action,
            resource,
            outcome,
            reason=reason,
            decision_id=decision_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
        )
        await self._sink.publish(topics.AUDIT, key=event.tenant_id, value=event)
        return event


async def emit_audit(
    sink,
    ctx: SecurityContext | None,
    action: str,
    resource: dict,
    outcome: str,
    reason: str | None = None,
    decision_id: UUID | None = None,
    trace_id: str | None = None,
    tenant_id: str | None = None,
) -> AuditEvent:
    """Convenience: emit a single audit event without holding an :class:`AuditEmitter`."""

    return await AuditEmitter(sink).emit(
        ctx,
        action,
        resource,
        outcome,
        reason=reason,
        decision_id=decision_id,
        trace_id=trace_id,
        tenant_id=tenant_id,
    )
