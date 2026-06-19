"""X4 pipeline-over-inproc test -- analyze_metric -> inproc bus -> findings + forecasts.

Drives the full L3 chain over the REAL in-process event bus
(``edis_platform.bus.inproc``, selected by ``sink_backend="inproc"``): a subscriber
on ``edis.findings.v1`` / ``edis.forecasts.v1`` / ``edis.governance.lineage.v1``
receives exactly the events the publisher emits, with the §4.3 keys, and the
payloads deserialize back to the typed contracts (``Finding`` / ``Forecast`` /
``LineageEvent``). No Docker, no broker, no API keys (FakeNarrator, stub embedder).

This proves the architecture's "downstream is identical regardless of backend":
the same publisher writes to the in-proc broker that, in prod, writes to Redpanda.
"""

from __future__ import annotations

import asyncio

import pytest

from edis_contracts import topics
from edis_contracts.events import LineageEvent
from edis_contracts.findings import Finding, Forecast

from edis_intelligence.grounding.embeddings import StubEmbedder
from edis_intelligence.rca.narrator import FakeNarrator, verify_grounding
from edis_intelligence.runner.pipeline import CandidateSeriesSpec, analyze_metric
from edis_intelligence.store.publisher import IntelligencePublisher
from edis_intelligence.store.repositories import InMemoryIntelligenceRepo

from edis_l3_testkit import make_demo_reader  # type: ignore[import-not-found]

_CANDIDATES = [
    CandidateSeriesSpec("latency_p95", {"region": "EMEA", "service": "checkout-api"}),
    CandidateSeriesSpec("error_rate", {"region": "EMEA", "service": "checkout-api"}),
]


async def _drain(stream, n: int, timeout: float = 5.0) -> list:
    """Collect ``n`` messages from an inproc subscription (with a safety timeout)."""

    out = []
    for _ in range(n):
        msg = await asyncio.wait_for(anext(stream), timeout=timeout)
        out.append(msg)
    return out


@pytest.mark.asyncio
async def test_analyze_metric_publishes_finding_and_forecast_over_inproc() -> None:
    from edis_platform.bus import make_sink, make_source, parse_message
    from edis_platform.bus.inproc import reset_brokers
    from edis_platform.settings import Settings

    reset_brokers()
    # ONE shared Settings instance so sink + source resolve the same in-proc broker.
    settings = Settings(sink_backend="inproc", anthropic_api_key=None, voyage_api_key=None)
    sink = make_sink(settings)
    source = make_source(settings)
    await sink.start()
    await source.start()

    # Subscribe BEFORE publishing so the inproc queues are registered (at-most-once).
    stream = source.subscribe([topics.FINDINGS, topics.FORECASTS, topics.LINEAGE], group="x4-test")

    reader = make_demo_reader()
    repo = InMemoryIntelligenceRepo()
    res = await analyze_metric(
        reader,
        "revenue",
        {"region": "EMEA", "channel": "web"},
        tenant_id="acme",
        candidates=_CANDIDATES,
        narrator=FakeNarrator(),  # grounded template echo -> source "llm"
        repo=repo,
        publisher=IntelligencePublisher(sink),
        embedder=StubEmbedder(),
    )

    assert res.detected and res.persisted and res.published
    assert res.finding is not None and res.forecast is not None

    # Three events: one finding, one forecast, one lineage.
    msgs = await _drain(stream, 3)
    by_topic = {m.topic: m for m in msgs}
    assert set(by_topic) == {topics.FINDINGS, topics.FORECASTS, topics.LINEAGE}

    # --- finding ---
    fmsg = by_topic[topics.FINDINGS]
    assert fmsg.key == f"acme:{res.finding.finding_id}"
    finding = parse_message(fmsg)
    assert isinstance(finding, Finding)
    assert finding.finding_id == res.finding.finding_id
    assert finding.metric_key == "revenue"
    assert finding.tenant_id == "acme"
    assert finding.kind.value == "root_cause"  # RCA attributed leading causes
    assert {c.metric_key for c in finding.candidate_causes} == {"latency_p95", "error_rate"}
    # grounding survived the round-trip
    ok, unmatched = verify_grounding(finding.narrative, res.bundle.allowed_numbers)
    assert ok, unmatched

    # --- forecast ---
    fcmsg = by_topic[topics.FORECASTS]
    assert fcmsg.key == "acme:revenue:channel=web&region=EMEA"
    forecast = parse_message(fcmsg)
    assert isinstance(forecast, Forecast)
    assert forecast.model == "statsmodels.ETS"
    assert forecast.forecast_id == res.forecast.forecast_id

    # --- lineage ---
    lmsg = by_topic[topics.LINEAGE]
    lineage = parse_message(lmsg)
    assert isinstance(lineage, LineageEvent)
    assert lineage.stage == "intelligence"
    assert any(o["type"] == "finding" for o in lineage.outputs)
    assert any(o["type"] == "forecast" for o in lineage.outputs)
    # inputs record the metric series read (target + the two candidate drivers)
    assert any(i.get("metric_key") == "revenue" for i in lineage.inputs)

    await source.stop()
    await sink.stop()


@pytest.mark.asyncio
async def test_no_anomaly_publishes_nothing() -> None:
    """A clean cell yields no finding -> analyze_metric publishes nothing."""

    from edis_platform.bus import make_sink, make_source
    from edis_platform.bus.inproc import reset_brokers
    from edis_platform.settings import Settings

    from edis_intelligence.runner.pipeline import InMemoryMetricReader

    from edis_l3_testkit import build_clean_series  # type: ignore[import-not-found]

    reset_brokers()
    settings = Settings(sink_backend="inproc")
    sink = make_sink(settings)
    source = make_source(settings)
    await sink.start()
    await source.start()
    stream = source.subscribe([topics.FINDINGS, topics.FORECASTS], group="x4-clean")

    reader = InMemoryMetricReader()
    reader.add_series("acme", "revenue", {"region": "NA"}, build_clean_series(), unit="USD")

    res = await analyze_metric(
        reader,
        "revenue",
        {"region": "NA"},
        tenant_id="acme",
        publisher=IntelligencePublisher(sink),
    )
    assert not res.detected
    assert not res.published

    # nothing was published -> the next() times out quickly
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(anext(stream), timeout=0.3)

    await source.stop()
    await sink.stop()
