"""Lineage emission -- record the input/output edges of one processing run.

Each layer that transforms data (integration, intelligence, decision) emits a
:class:`~edis_contracts.events.LineageEvent` to ``edis.governance.lineage.v1``
naming the records it read (``inputs``) and produced (``outputs``) under a single
``run_id``. The governance service folds these into the ``lineage_edge`` table so
any canonical fact can be traced back to its raw sources.

Like :class:`~edis_gov_sdk.audit.AuditEmitter`, this owns no DB: it publishes
through an injected :class:`~edis_platform.bus.base.EventSink`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from edis_contracts import topics
from edis_contracts.events import LineageEvent


def _utc_now() -> datetime:
    """Timezone-aware UTC now (every EDIS timestamp is tz-aware UTC)."""

    return datetime.now(timezone.utc)


def build_lineage_event(
    tenant_id: str,
    run_id: UUID,
    inputs: list[dict],
    outputs: list[dict],
    stage: str,
) -> LineageEvent:
    """Construct a :class:`LineageEvent` with a fresh ``lineage_id`` and UTC ``occurred_at``."""

    return LineageEvent(
        lineage_id=uuid4(),
        tenant_id=tenant_id,
        run_id=run_id,
        inputs=list(inputs),
        outputs=list(outputs),
        stage=stage,
        occurred_at=_utc_now(),
    )


class LineageEmitter:
    """Publishes :class:`LineageEvent`\\ s to ``edis.governance.lineage.v1`` via a sink."""

    def __init__(self, sink) -> None:
        self._sink = sink

    async def emit(
        self,
        tenant_id: str,
        run_id: UUID,
        inputs: list[dict],
        outputs: list[dict],
        stage: str,
    ) -> LineageEvent:
        """Build and publish a :class:`LineageEvent`, returning the built event.

        Keyed by ``tenant_id`` on the bus (per the topic contract).
        """

        event = build_lineage_event(tenant_id, run_id, inputs, outputs, stage)
        await self._sink.publish(topics.LINEAGE, key=event.tenant_id, value=event)
        return event


async def emit_lineage(
    sink,
    tenant_id: str,
    run_id: UUID,
    inputs: list[dict],
    outputs: list[dict],
    stage: str,
) -> LineageEvent:
    """Convenience: emit a single lineage event without holding a :class:`LineageEmitter`."""

    return await LineageEmitter(sink).emit(tenant_id, run_id, inputs, outputs, stage)
