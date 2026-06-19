"""X3 unit tests — analyze_metric end-to-end, in-memory repo, and the publisher.

Exercises the whole IO chain (detect -> score -> RCA -> evidence -> narrate ->
forecast -> persist -> publish) over the §9 demo series with an in-memory reader, a
FakeNarrator, an in-memory repo, and an in-memory publisher — no Docker, no API keys.
Asserts the grounding guarantee survives the full pipeline and that persistence +
publication carry the finding/forecast/lineage.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import numpy as np
import pytest

from edis_contracts import topics
from edis_contracts.events import LineageEvent
from edis_contracts.findings import Finding, Forecast

from edis_intelligence.grounding.embeddings import StubEmbedder
from edis_intelligence.rca.narrator import FakeNarrator, verify_grounding
from edis_intelligence.runner.pipeline import (
    CandidateSeriesSpec,
    InMemoryMetricReader,
    analyze_metric,
)
from edis_intelligence.store.publisher import IntelligencePublisher
from edis_intelligence.store.repositories import InMemoryIntelligenceRepo

_START = datetime(2026, 5, 15, tzinfo=timezone.utc)
_WEEKLY = [1.05, 1.0, 0.98, 1.02, 1.1, 0.92, 0.93]
_BASELINE_DAYS = 28
_INCIDENT_DAYS = 7


def _demo_reader() -> InMemoryMetricReader:
    """Build the §9 demo: EMEA-web revenue level shift + leading latency/error spikes."""

    rng = np.random.default_rng(42)
    n = _BASELINE_DAYS + _INCIDENT_DAYS
    days = [_START + timedelta(days=i) for i in range(n)]
    rev, lat, err = [], [], []
    for i, d in enumerate(days):
        base = 95_000 * _WEEKLY[d.weekday()] + rng.normal(0, 1500)
        if i >= _BASELINE_DAYS:
            base *= 0.64
        rev.append((d, base))
        if i >= _BASELINE_DAYS - 1:
            lat.append((d, 1400 + rng.normal(0, 40)))
            err.append((d, 0.09 + rng.normal(0, 0.005)))
        else:
            lat.append((d, 180 + rng.normal(0, 10)))
            err.append((d, 0.004 + rng.normal(0, 0.001)))

    reader = InMemoryMetricReader()
    dims = {"region": "EMEA", "channel": "web"}
    reader.add_series("acme", "revenue", dims, rev, unit="USD")
    reader.add_series(
        "acme", "latency_p95", {"region": "EMEA", "service": "checkout-api"}, lat, unit="ms"
    )
    reader.add_series(
        "acme", "error_rate", {"region": "EMEA", "service": "checkout-api"}, err, unit="pct"
    )
    return reader


class _CapturingSink:
    """Minimal EventSink capturing published (topic, key, value) tuples."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str | None, object]] = []

    async def start(self) -> None:  # pragma: no cover - trivial
        pass

    async def stop(self) -> None:  # pragma: no cover - trivial
        pass

    async def publish(self, topic, key, value) -> None:
        self.published.append((topic, key, value))


@pytest.mark.asyncio
async def test_analyze_metric_detects_and_narrates_grounded() -> None:
    reader = _demo_reader()
    res = await analyze_metric(
        reader,
        "revenue",
        {"region": "EMEA", "channel": "web"},
        tenant_id="acme",
        candidates=[
            CandidateSeriesSpec("latency_p95", {"region": "EMEA", "service": "checkout-api"}),
            CandidateSeriesSpec("error_rate", {"region": "EMEA", "service": "checkout-api"}),
        ],
        narrator=FakeNarrator(),  # echoes the grounded template -> source "llm"
    )

    assert res.detected
    assert res.finding is not None
    assert res.finding.metric_key == "revenue"
    assert res.finding.deviation < 0  # revenue fell
    # RCA found leading causes -> finding is a ROOT_CAUSE
    assert res.candidate_causes, "RCA must surface leading causes"
    assert res.finding.kind.value == "root_cause"
    assert {c.metric_key for c in res.candidate_causes} == {"latency_p95", "error_rate"}

    # narrative present and grounded against the bundle whitelist
    assert res.finding.narrative
    ok, unmatched = verify_grounding(res.finding.narrative, res.bundle.allowed_numbers)
    assert ok, unmatched
    assert res.finding.evidence_ref == res.bundle.bundle_id

    # forecast band attached
    assert isinstance(res.forecast, Forecast)
    assert res.forecast.model == "statsmodels.ETS"


@pytest.mark.asyncio
async def test_analyze_metric_no_anomaly_returns_no_finding() -> None:
    reader = InMemoryMetricReader()
    days = [_START + timedelta(days=i) for i in range(35)]
    flat = [(d, 100.0 + _WEEKLY[d.weekday()]) for d in days]  # stable, seasonal
    reader.add_series("acme", "revenue", {"region": "NA"}, flat, unit="USD")
    res = await analyze_metric(reader, "revenue", {"region": "NA"}, tenant_id="acme")
    assert not res.detected
    assert res.finding is None


@pytest.mark.asyncio
async def test_analyze_metric_persists_and_publishes() -> None:
    reader = _demo_reader()
    repo = InMemoryIntelligenceRepo()
    sink = _CapturingSink()
    publisher = IntelligencePublisher(sink)

    res = await analyze_metric(
        reader,
        "revenue",
        {"region": "EMEA", "channel": "web"},
        tenant_id="acme",
        candidates=[
            CandidateSeriesSpec("latency_p95", {"region": "EMEA", "service": "checkout-api"}),
        ],
        narrator=FakeNarrator(),
        repo=repo,
        publisher=publisher,
        embedder=StubEmbedder(),
    )

    assert res.persisted and res.published

    # persisted finding + bundle readable back, tenant-scoped
    fetched = await repo.get_finding("acme", res.finding.finding_id)
    assert isinstance(fetched, Finding)
    assert fetched.evidence_ref == res.bundle.bundle_id
    bundle = await repo.get_evidence_bundle("acme", res.bundle.bundle_id)
    assert bundle is not None and bundle.finding_id == res.finding.finding_id
    # wrong tenant cannot read it
    assert await repo.get_finding("other", res.finding.finding_id) is None

    # embedding persisted with provenance
    stored = repo.stored_finding(res.finding.finding_id)
    assert stored.embedding is not None and len(stored.embedding) == 1024
    assert stored.embedding_model == "stub-hash-1024"

    # forecast persisted + listed
    forecasts = await repo.list_forecasts("acme")
    assert len(forecasts) == 1

    # published findings + forecasts + a lineage event
    pub_topics = [t for (t, _k, _v) in sink.published]
    assert topics.FINDINGS in pub_topics
    assert topics.FORECASTS in pub_topics
    assert topics.LINEAGE in pub_topics
    # finding keyed tenant:finding_id
    fkey = next(k for (t, k, _v) in sink.published if t == topics.FINDINGS)
    assert fkey == f"acme:{res.finding.finding_id}"
    # lineage carries the intelligence stage + finding output
    lineage = next(v for (t, _k, v) in sink.published if t == topics.LINEAGE)
    assert isinstance(lineage, LineageEvent)
    assert lineage.stage == "intelligence"
    assert any(o["type"] == "finding" for o in lineage.outputs)


@pytest.mark.asyncio
async def test_finding_publishes_with_null_narrative_when_narration_unavailable() -> None:
    """A finding still persists/publishes when the narrator yields template text only.

    Narration never blocks detection: with no LLM client the template narrator runs and
    narrative_model is None — the finding is unchanged otherwise and still published.
    """

    reader = _demo_reader()
    repo = InMemoryIntelligenceRepo()
    sink = _CapturingSink()
    res = await analyze_metric(
        reader,
        "revenue",
        {"region": "EMEA", "channel": "web"},
        tenant_id="acme",
        narrator=None,  # -> TemplateNarrator
        repo=repo,
        publisher=IntelligencePublisher(sink),
    )
    assert res.detected
    assert res.finding.narrative_model is None  # template path
    assert res.finding.narrative  # but text is present
    assert res.narration.source == "template"
    assert topics.FINDINGS in [t for (t, _k, _v) in sink.published]


@pytest.mark.asyncio
async def test_in_memory_repo_pagination_and_filter() -> None:
    repo = InMemoryIntelligenceRepo()
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    for i in range(5):
        f = Finding(
            finding_id=uuid4(),
            tenant_id="acme",
            kind="point_anomaly",
            metric_key="error_rate" if i % 2 else "revenue",
            dimensions={},
            window_start=base,
            window_end=base,
            detector="robust_zscore",
            detector_version="1.0",
            observed_value=1.0,
            expected_value=0.5,
            deviation=0.5,
            deviation_pct=100.0,
            score=4.0,
            severity=0.5,
            confidence=0.5,
            business_impact_input=0.5,
            status="open" if i < 3 else "resolved",
            created_at=base + timedelta(minutes=i),
        )
        await repo.save_finding(f)

    all_open = await repo.list_findings("acme", status="open")
    assert len(all_open) == 3
    rev_only = await repo.list_findings("acme", metric_key="revenue")
    assert all(f.metric_key == "revenue" for f in rev_only)
    page = await repo.list_findings("acme", limit=2, offset=0)
    assert len(page) == 2
    # newest first
    assert page[0].created_at >= page[1].created_at
    # tenant isolation
    assert await repo.list_findings("ghost") == []
