"""Thin publisher for L4 events: recommendations + lifecycle transitions.

:class:`DecisionEventProducer` wraps an injected
:class:`~edis_platform.bus.base.EventSink` and publishes the two L4 payloads on the
correct topics with the correct partition keys (per §4.3):

* :class:`~edis_contracts.decisions.Recommendation` ->
  ``edis.decisions.recommendations.v1`` keyed by ``tenant_id`` (per-tenant ordering).
* :class:`~edis_contracts.decisions.RecommendationLifecycleEvent` ->
  ``edis.decisions.lifecycle.v1`` keyed by ``recommendation_id`` (one recommendation's
  transitions stay ordered on one partition).

It owns no sink lifecycle (the service starts/stops the shared sink) and connects to
nothing at import. Keeping this in one place means the keying rule lives in exactly one
spot, shared by the finding consumer (C1) and the lifecycle manager (C2).
"""

from __future__ import annotations

from edis_contracts.decisions import Recommendation, RecommendationLifecycleEvent
from edis_platform.bus.base import EventSink

from decision_engine.events import topics


class DecisionEventProducer:
    """Publishes L4 recommendations + lifecycle events on their canonical topics."""

    def __init__(self, sink: EventSink) -> None:
        self._sink = sink

    async def publish_recommendation(self, rec: Recommendation) -> None:
        """Publish a recommendation keyed by ``tenant_id`` (per §4.3)."""

        await self._sink.publish(topics.RECOMMENDATIONS, key=rec.tenant_id, value=rec)

    async def publish_lifecycle(self, event: RecommendationLifecycleEvent) -> None:
        """Publish a lifecycle transition keyed by ``recommendation_id`` (per §4.3)."""

        await self._sink.publish(
            topics.DECISIONS_LIFECYCLE,
            key=str(event.recommendation_id),
            value=event,
        )
