"""Unit test: synthesize the demo finding -> a fully-scored Recommendation, published.

Two layers, both no infra / no key:

* the pure :func:`synthesize` over the §9 demo finding yields the expected
  ``operational_fix`` recommendation (impact in $120K-$200K, confidence 0.8-0.9, rank 1),
  with EVERY number from the deterministic scoring core;
* the :class:`FindingConsumer` publishes that recommendation to
  ``edis.decisions.recommendations.v1`` over the REAL in-process bus (round-tripped through
  JSON and parsed back to a :class:`Recommendation`), plus the initial ``proposed``
  lifecycle event and the governance audit/lineage edges.
"""

from __future__ import annotations

from edis_contracts import topics
from edis_contracts.decisions import Recommendation, RecommendationLifecycleEvent
from edis_platform.bus.base import parse_message
from edis_platform.bus.inproc import (
    InProcEventSink,
    InProcMessageSource,
    reset_brokers,
)
from edis_platform.settings import Settings

from decision_engine.consumers.finding_consumer import FindingConsumer
from decision_engine.synthesis.synthesizer import synthesize

from edis_l4_testkit import DEMO_TENANT, build_demo_finding


async def test_synthesize_demo_finding_full_recommendation(fixed_now):
    """The pure synthesize core: demo finding -> the expected scored recommendation."""

    rec = await synthesize(build_demo_finding(), now=fixed_now)

    assert isinstance(rec, Recommendation)
    assert rec.tenant_id == DEMO_TENANT
    assert rec.source_finding_id == build_demo_finding().finding_id
    assert rec.action_type == "operational_fix"
    assert rec.playbook_id == "operational_fix"
    assert rec.playbook_version == "1.0"
    assert rec.effort_tier == "s"
    assert rec.status == "proposed"

    # The numbers (deterministic; never the LLM).
    assert rec.impact.value == 170000.0
    assert 120000.0 <= rec.impact.value <= 200000.0
    assert rec.impact.method == "recovery_flat"
    assert rec.impact.inputs == {"daily_loss": 34000.0, "affected_days_remaining": 5.0}
    assert 0.8 <= rec.confidence.value <= 0.9
    assert rec.confidence.calibration_n == 0
    assert rec.priority_rank == 1
    assert 0.9 <= rec.priority_score <= 0.95

    # Bound to the failing service + region from the leading candidate cause.
    assert rec.action_params["service"] == "checkout-api"
    assert rec.action_params["region"] == "EMEA"

    # Provenance + a grounded (non-LLM) summary; narrative not attached here.
    assert rec.narrative is None
    assert any(link["type"] == "finding" for link in rec.evidence_trail)
    # The summary is grounded in the deterministic figures (formatted with thousands sep).
    assert "170,000" in rec.explanation_summary


async def test_synthesize_is_deterministic(fixed_now):
    """Same finding + now + fixed id -> byte-identical recommendation (no LLM, no clock)."""

    fid = build_demo_finding().finding_id
    a = await synthesize(build_demo_finding(), now=fixed_now, recommendation_id=fid)
    b = await synthesize(build_demo_finding(), now=fixed_now, recommendation_id=fid)
    assert a.model_dump() == b.model_dump()


async def test_finding_consumer_publishes_recommendation_over_inproc_bus():
    """The consumer publishes the recommendation to the recommendations topic (inproc)."""

    reset_brokers()
    settings = Settings(sink_backend="inproc")
    sink = InProcEventSink(settings)
    source = InProcMessageSource(settings)
    await sink.start()
    await source.start()

    # Subscribe BEFORE publishing so the inproc queue is registered (see inproc docs).
    stream = source.subscribe([topics.RECOMMENDATIONS], group="test-recs")

    consumer = FindingConsumer(source, sink)
    rec = await consumer.handle_finding(build_demo_finding())

    # The published recommendation round-trips back through the bus as a Recommendation.
    msg = await _anext_with_timeout(stream)
    published = parse_message(msg)
    assert isinstance(published, Recommendation)
    assert published.recommendation_id == rec.recommendation_id
    assert published.action_type == "operational_fix"
    assert published.priority_rank == 1
    # Confidence is independent of the clock (no time inputs) -> stays in the demo band.
    assert 0.8 <= published.confidence.value <= 0.9
    # Impact is positive recovery via the deterministic recovery_flat method (the exact
    # dollar band depends on `now` vs the window; the pinned-now test asserts $120K-$200K).
    assert published.impact.method == "recovery_flat"
    assert published.impact.value > 0.0
    assert published.impact.inputs["daily_loss"] == 34000.0

    # Keyed by tenant_id per §4.3.
    assert msg.key == DEMO_TENANT

    await source.stop()
    await sink.stop()


async def test_finding_consumer_emits_lifecycle_audit_and_lineage(fake_sink):
    """Beyond the recommendation, the consumer emits proposed-lifecycle + audit + lineage."""

    consumer = FindingConsumer(source=_NullSource(), sink=fake_sink)
    await consumer.handle_finding(build_demo_finding())

    published_topics = fake_sink.topics_published()
    assert topics.RECOMMENDATIONS in published_topics
    assert topics.DECISIONS_LIFECYCLE in published_topics
    assert topics.AUDIT in published_topics
    assert topics.LINEAGE in published_topics

    # The initial lifecycle is None -> proposed, keyed by recommendation_id.
    lifecycle_values = fake_sink.values_for(topics.DECISIONS_LIFECYCLE)
    assert len(lifecycle_values) == 1
    evt = RecommendationLifecycleEvent.model_validate(lifecycle_values[0])
    assert evt.from_status is None
    assert evt.to_status == "proposed"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
async def _anext_with_timeout(stream, timeout: float = 2.0):
    import asyncio

    return await asyncio.wait_for(stream.__anext__(), timeout=timeout)


class _NullSource:
    """A MessageSource stand-in for handle_finding-only tests (run() is never called)."""

    async def start(self) -> None:  # pragma: no cover - unused
        ...

    async def stop(self) -> None:  # pragma: no cover - unused
        ...

    def subscribe(self, topics_, group):  # pragma: no cover - unused
        raise NotImplementedError
