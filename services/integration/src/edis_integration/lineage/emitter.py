"""Integration-stage lineage: raw_event -> canonical -> metric edges.

Every L2 run records what it read (the raw :class:`IngestEnvelope`) and what it
produced (canonical orders/customers, ops events, metric observations) as one
:class:`~edis_contracts.events.LineageEvent` on ``edis.governance.lineage.v1``,
keyed by ``tenant_id`` (per the topic contract). Governance folds these into the
``lineage_edge`` graph so any canonical fact -- or any metric point -- traces back
to its raw source for "why is this number what it is".

This builds on the platform :class:`edis_gov_sdk.lineage.LineageEmitter` (which
owns the publish) and adds the integration-specific *edge construction*: the
input/output node descriptors for this stage. The engine stages the lineage event
into the transactional outbox (so it commits atomically with the canonical rows
and the relay publishes it); :func:`build_integration_lineage` is the single place
that shapes those edges, and :class:`IntegrationLineageEmitter` is the direct
sink-publish path used by tooling/backfill where the outbox is not involved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from edis_contracts.events import LineageEvent
from edis_gov_sdk.lineage import LineageEmitter as _SdkLineageEmitter
from edis_gov_sdk.lineage import build_lineage_event

if TYPE_CHECKING:
    from edis_contracts.canonical import (
        CanonicalCustomer,
        CanonicalOrder,
        MetricObservation,
        OpsEvent,
    )
    from edis_contracts.ingest import IngestEnvelope
    from edis_platform.bus.base import EventSink

_STAGE = "integration"


def _metric_node_id(obs: "MetricObservation") -> str:
    """Stable node id for a metric output: ``tenant:metric_key:dim_hash:ts``."""

    dim_hash = "&".join(f"{k}={v}" for k, v in sorted(obs.dimensions.items()))
    return f"{obs.tenant_id}:{obs.metric_key}:{dim_hash}:{obs.ts.isoformat()}"


def build_integration_lineage(
    envelope: "IngestEnvelope",
    *,
    run_id: UUID,
    orders: "list[CanonicalOrder]",
    customers: "list[CanonicalCustomer]",
    ops_events: "list[OpsEvent]",
    metrics: "list[MetricObservation]" = (),  # type: ignore[assignment]
) -> LineageEvent:
    """Shape the raw_event -> {canonical, metric} edges for one integration run.

    ``inputs`` is the raw event; ``outputs`` are every canonical entity and metric
    point produced. Node descriptors are ``{type, id}`` dicts (the
    :class:`LineageEvent` contract), with stable, dereferenceable ids.
    """

    inputs = [{"type": "raw_event", "id": str(envelope.event_id)}]
    outputs: list[dict] = []
    outputs += [{"type": "canonical_order", "id": str(o.canonical_order_id)} for o in orders]
    outputs += [
        {"type": "canonical_customer", "id": str(c.canonical_customer_id)} for c in customers
    ]
    outputs += [{"type": "ops_event", "id": str(e.canonical_ops_event_id)} for e in ops_events]
    outputs += [{"type": "metric_observation", "id": _metric_node_id(m)} for m in metrics]
    return build_lineage_event(
        tenant_id=envelope.tenant_id,
        run_id=run_id,
        inputs=inputs,
        outputs=outputs,
        stage=_STAGE,
    )


class IntegrationLineageEmitter:
    """Publish integration-stage lineage directly via a sink (non-outbox path).

    Wraps the governance-SDK :class:`LineageEmitter`. The live pipeline routes
    lineage through the transactional outbox instead (atomic with the canonical
    write); this direct emitter is for tooling/backfill and tests.
    """

    def __init__(self, sink: "EventSink") -> None:
        self._sdk = _SdkLineageEmitter(sink)

    async def emit(
        self,
        envelope: "IngestEnvelope",
        *,
        run_id: UUID,
        orders: "list[CanonicalOrder]",
        customers: "list[CanonicalCustomer]",
        ops_events: "list[OpsEvent]",
        metrics: "list[MetricObservation]" = (),  # type: ignore[assignment]
    ) -> LineageEvent:
        """Build the integration edges and publish them via the SDK emitter."""

        return await self._sdk.emit(
            tenant_id=envelope.tenant_id,
            run_id=run_id,
            inputs=[{"type": "raw_event", "id": str(envelope.event_id)}],
            outputs=build_integration_lineage(
                envelope,
                run_id=run_id,
                orders=orders,
                customers=customers,
                ops_events=ops_events,
                metrics=metrics,
            ).outputs,
            stage=_STAGE,
        )


__all__ = [
    "build_integration_lineage",
    "IntegrationLineageEmitter",
]
