"""X4 integration -- persist + read a Finding (with embedding) against Postgres+pgvector.

``@pytest.mark.integration`` (Docker required; excluded from ``pytest -m "not
integration"``). Exercises the real :class:`SqlAlchemyIntelligenceRepo` over the
shipped Alembic schema:

* an EvidenceBundle + Finding (with candidate causes + a grounded narrative + a
  1024-d embedding) round-trip through the DB and read back equal to the contract
  payloads, tenant-scoped (a wrong tenant cannot read them);
* the ``findings.embedding`` column persists the vector (written via explicit SQL so
  it works whether the column is pgvector ``vector`` or the jsonb degrade);
* a Forecast persists + lists; replayed saves are idempotent (ON CONFLICT upsert).

The whole analyze chain is also run end-to-end into the real repo so the demo
finding lands in Postgres exactly as the pipeline would write it in prod.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from edis_contracts.findings import (
    CandidateCause,
    EvidenceBundle,
    EvidenceItem,
    Finding,
    Forecast,
)

from edis_intelligence.grounding.embeddings import StubEmbedder, stub_embedding
from edis_intelligence.rca.narrator import FakeNarrator
from edis_intelligence.runner.pipeline import CandidateSeriesSpec, analyze_metric
from edis_intelligence.store.repositories import SqlAlchemyIntelligenceRepo

from conftest import make_demo_reader  # type: ignore[import-not-found]

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 19, tzinfo=timezone.utc)


def _bundle(finding_id):
    return EvidenceBundle(
        bundle_id=uuid4(),
        tenant_id="acme",
        finding_id=finding_id,
        created_at=_NOW,
        items=[
            EvidenceItem(
                kind="metric_window",
                metric_key="revenue",
                dimensions={"region": "EMEA"},
                summary="revenue (region=EMEA) was 61,000 vs an expected 95,000 (-35.8%).",
                values={
                    "observed_value": 61000.0,
                    "expected_value": 95000.0,
                    "deviation_pct": -35.8,
                },
            )
        ],
        allowed_numbers=[61000.0, 95000.0, -35.8, 35.8],
    )


def _finding(finding_id, bundle_id, tenant="acme"):
    return Finding(
        finding_id=finding_id,
        tenant_id=tenant,
        kind="level_shift",
        metric_key="revenue",
        dimensions={"region": "EMEA", "channel": "web"},
        window_start=_NOW - timedelta(days=7),
        window_end=_NOW,
        detector="stl_seasonal",
        detector_version="1.0",
        observed_value=61000.0,
        expected_value=95000.0,
        deviation=-34000.0,
        deviation_pct=-35.8,
        score=5.8,
        severity=0.86,
        confidence=0.91,
        business_impact_input=0.78,
        candidate_causes=[
            CandidateCause(
                metric_key="latency_p95",
                dimensions={"region": "EMEA", "service": "checkout-api"},
                correlation=-0.94,
                lag_minutes=120,
                contribution_pct=71.0,
                direction="leading",
                observed_delta=1220.0,
            ),
        ],
        narrative="EMEA web revenue fell to 61,000 from 95,000 (-35.8%).",
        narrative_model=None,
        evidence_ref=bundle_id,
        status="open",
        created_at=_NOW,
    )


@pytest.mark.asyncio
async def test_persist_and_read_finding_with_embedding(pg_sessionmaker) -> None:
    repo = SqlAlchemyIntelligenceRepo(pg_sessionmaker)

    fid = uuid4()
    bundle = _bundle(fid)
    finding = _finding(fid, bundle.bundle_id)
    embedding = stub_embedding("revenue dropped in EMEA web; checkout-api latency spike")
    assert len(embedding) == 1024

    await repo.save_finding(finding, bundle, embedding=embedding, embedding_model="stub-hash-1024")

    # read back, tenant-scoped
    got = await repo.get_finding("acme", fid)
    assert got is not None
    assert got.finding_id == fid
    assert got.kind.value == "level_shift"
    assert got.observed_value == 61000.0
    assert got.evidence_ref == bundle.bundle_id
    assert len(got.candidate_causes) == 1
    assert got.candidate_causes[0].metric_key == "latency_p95"
    assert got.candidate_causes[0].direction == "leading"

    # wrong tenant cannot read it
    assert await repo.get_finding("ghost", fid) is None

    # evidence bundle round-trips with its whitelist
    eb = await repo.get_evidence_bundle("acme", bundle.bundle_id)
    assert eb is not None
    assert eb.finding_id == fid
    assert 61000.0 in eb.allowed_numbers

    # the embedding column actually stored the vector (read it back via raw SQL)
    from sqlalchemy import text

    async with pg_sessionmaker() as session:
        row = (
            await session.execute(
                text("SELECT embedding::text FROM findings WHERE finding_id = :fid"),
                {"fid": str(fid)},
            )
        ).first()
    assert row is not None and row[0] is not None
    # the stored representation contains the 1024 components (vector or jsonb literal)
    assert row[0].count(",") == 1023


@pytest.mark.asyncio
async def test_list_findings_tenant_scoped_and_filtered(pg_sessionmaker) -> None:
    repo = SqlAlchemyIntelligenceRepo(pg_sessionmaker)
    for i in range(3):
        fid = uuid4()
        b = _bundle(fid)
        await repo.save_finding(_finding(fid, b.bundle_id), b)
    ghost_id = uuid4()
    gb = _bundle(ghost_id)
    await repo.save_finding(_finding(ghost_id, gb.bundle_id, tenant="ghost"), gb)

    acme = await repo.list_findings("acme")
    assert len(acme) == 3
    assert all(f.tenant_id == "acme" for f in acme)
    assert await repo.list_findings("ghost") and len(await repo.list_findings("ghost")) == 1
    # metric filter
    rev = await repo.list_findings("acme", metric_key="revenue")
    assert len(rev) == 3
    assert await repo.list_findings("acme", metric_key="orders") == []


@pytest.mark.asyncio
async def test_save_is_idempotent_on_replay(pg_sessionmaker) -> None:
    repo = SqlAlchemyIntelligenceRepo(pg_sessionmaker)
    fid = uuid4()
    b = _bundle(fid)
    f = _finding(fid, b.bundle_id)
    await repo.save_finding(f, b)
    # replay the same finding -> ON CONFLICT upsert, still exactly one row
    await repo.save_finding(f, b)
    rows = await repo.list_findings("acme")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_persist_and_list_forecast(pg_sessionmaker) -> None:
    repo = SqlAlchemyIntelligenceRepo(pg_sessionmaker)
    fc = Forecast(
        forecast_id=uuid4(),
        tenant_id="acme",
        metric_key="revenue",
        dimensions={"region": "EMEA", "channel": "web"},
        model="statsmodels.ETS",
        horizon_days=7,
        points=[
            {
                "ts": "2026-06-20T00:00:00+00:00",
                "yhat": 60000.0,
                "yhat_lower": 50000.0,
                "yhat_upper": 70000.0,
            }
        ],
        generated_at=_NOW,
    )
    await repo.save_forecast(fc)
    got = await repo.list_forecasts("acme")
    assert len(got) == 1
    assert got[0].model == "statsmodels.ETS"
    assert got[0].points[0]["yhat"] == 60000.0
    assert await repo.list_forecasts("ghost") == []


@pytest.mark.asyncio
async def test_full_pipeline_persists_to_postgres(pg_sessionmaker) -> None:
    """Run analyze_metric end-to-end into the REAL repo: the demo finding lands in PG."""

    repo = SqlAlchemyIntelligenceRepo(pg_sessionmaker)
    reader = make_demo_reader()
    res = await analyze_metric(
        reader,
        "revenue",
        {"region": "EMEA", "channel": "web"},
        tenant_id="acme",
        candidates=[
            CandidateSeriesSpec("latency_p95", {"region": "EMEA", "service": "checkout-api"}),
            CandidateSeriesSpec("error_rate", {"region": "EMEA", "service": "checkout-api"}),
        ],
        narrator=FakeNarrator(),
        repo=repo,
        embedder=StubEmbedder(),
    )
    assert res.detected and res.persisted

    got = await repo.get_finding("acme", res.finding.finding_id)
    assert got is not None
    assert got.kind.value == "root_cause"
    assert {c.metric_key for c in got.candidate_causes} == {"latency_p95", "error_rate"}
    eb = await repo.get_evidence_bundle("acme", res.bundle.bundle_id)
    assert eb is not None and eb.finding_id == res.finding.finding_id
