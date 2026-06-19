"""Reactive consumer on ``edis.findings.v1`` -- the L4 trigger.

Subscribes to the findings topic via :func:`~edis_platform.bus.base.make_source`, runs
the pure :func:`~decision_engine.synthesis.synthesizer.synthesize` core on each
:class:`~edis_contracts.findings.Finding`, then publishes the resulting
:class:`~edis_contracts.decisions.Recommendation` to
``edis.decisions.recommendations.v1`` (keyed by ``tenant_id`` per §4.3), emits the
initial ``proposed`` lifecycle event to ``edis.decisions.lifecycle.v1``, and emits an
``AI_DECISION`` audit event via the governance SDK.

Persistence (the SQLAlchemy repo) is an injected, optional collaborator -- when present
the recommendation is saved before publish; when absent (CI / the bare app) the consumer
still runs the full synthesize -> publish chain in-memory, so the demo works with no DB.

Collaborators are injected; :meth:`run` loops until :meth:`stop`. Building it connects to
nothing. One bad finding never kills the consumer -- synthesis/publish errors are caught,
logged, and the loop continues.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from edis_contracts import topics
from edis_contracts.decisions import Recommendation, RecommendationLifecycleEvent
from edis_contracts.findings import Finding
from edis_platform.bus.base import MessageSource, parse_message
from edis_platform.logging import get_logger
from edis_gov_sdk.audit import emit_audit
from edis_gov_sdk.lineage import emit_lineage

from decision_engine.synthesis.intent_classifier import Classifier, RuleBasedIntentClassifier
from decision_engine.synthesis.playbook_registry import PlaybookRegistry
from decision_engine.synthesis.synthesizer import synthesize

_log = get_logger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FindingConsumer:
    """Consumes ``edis.findings.v1`` -> synthesize -> persist? -> publish + audit/lineage."""

    def __init__(
        self,
        source: MessageSource,
        sink,
        *,
        classifier: Classifier | None = None,
        registry: PlaybookRegistry | None = None,
        repo=None,
        scorer=None,
        retriever=None,
        estimator=None,
        prioritizer=None,
        group: str = "edis-decision",
        ttl_hours: int = 72,
    ) -> None:
        self._source = source
        self._sink = sink
        self._classifier = classifier or RuleBasedIntentClassifier()
        self._registry = registry or PlaybookRegistry()
        self._repo = repo
        self._scorer = scorer
        self._retriever = retriever
        self._estimator = estimator
        self._prioritizer = prioritizer
        self._group = group
        self._ttl_hours = int(ttl_hours)
        self._running = False

    async def handle_finding(self, finding: Finding) -> Recommendation:
        """Synthesize, (optionally) persist, then publish one recommendation.

        Returns the published :class:`Recommendation` so callers/tests can assert on it.
        Every number on it comes from the deterministic scoring core.
        """

        rec = await synthesize(
            finding,
            classifier=self._classifier,
            registry=self._registry,
            retriever=self._retriever,
            estimator=self._estimator,
            scorer=self._scorer,
            prioritizer=self._prioritizer,
            ttl_hours=self._ttl_hours,
        )

        # Persist before publish when a repo is wired (no "published-but-not-stored" gap
        # in the DB deployment; the in-memory path simply skips this).
        if self._repo is not None:
            await self._repo.save_recommendation(rec)

        # Publish the recommendation (key=tenant_id per the §4.3 contract).
        await self._sink.publish(topics.RECOMMENDATIONS, key=rec.tenant_id, value=rec)

        # Emit the initial lifecycle transition (None -> proposed).
        lifecycle = RecommendationLifecycleEvent(
            event_id=uuid4(),
            tenant_id=rec.tenant_id,
            recommendation_id=rec.recommendation_id,
            from_status=None,
            to_status="proposed",
            actor={"type": "system", "id": "decision-engine"},
            occurred_at=_utc_now(),
        )
        await self._sink.publish(
            topics.DECISIONS_LIFECYCLE, key=str(rec.recommendation_id), value=lifecycle
        )

        # Governance: audit the AI decision + a lineage edge (finding -> recommendation).
        await emit_audit(
            self._sink,
            None,  # system actor (no principal in the background consumer)
            "AI_DECISION",
            {"type": "recommendation", "id": str(rec.recommendation_id)},
            "ALLOW",
            tenant_id=rec.tenant_id,
            reason=f"playbook={rec.playbook_id} action={rec.action_type}",
        )
        await emit_lineage(
            self._sink,
            tenant_id=rec.tenant_id,
            run_id=uuid4(),
            inputs=[{"type": "finding", "id": str(finding.finding_id)}],
            outputs=[{"type": "recommendation", "id": str(rec.recommendation_id)}],
            stage="decision",
        )

        _log.info(
            "recommendation synthesized",
            extra={
                "tenant_id": rec.tenant_id,
                "recommendation_id": str(rec.recommendation_id),
                "source_finding_id": str(finding.finding_id),
                "action_type": rec.action_type,
                "playbook_id": rec.playbook_id,
                "impact_value": rec.impact.value,
                "confidence_value": rec.confidence.value,
                "priority_rank": rec.priority_rank,
            },
        )
        return rec

    async def run(self) -> None:
        """Subscribe to ``edis.findings.v1`` and synthesize reactively until :meth:`stop`."""

        self._running = True
        await self._source.start()
        _log.info("finding consumer started", extra={"group": self._group})
        try:
            async for msg in self._source.subscribe([topics.FINDINGS], group=self._group):
                if not self._running:
                    break
                parsed = parse_message(msg)
                if not isinstance(parsed, Finding):
                    continue
                try:
                    await self.handle_finding(parsed)
                except Exception as exc:  # noqa: BLE001 - one bad finding must not kill the loop
                    _log.warning(
                        "finding synthesis failed",
                        extra={"finding_id": str(parsed.finding_id), "error": str(exc)},
                    )
        finally:
            await self._source.stop()
            _log.info("finding consumer stopped")

    async def stop(self) -> None:
        """Signal the run loop to exit and stop the source."""

        self._running = False
        await self._source.stop()
