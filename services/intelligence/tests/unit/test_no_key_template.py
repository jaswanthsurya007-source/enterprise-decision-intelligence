"""X4 no-key test -- with no ANTHROPIC_API_KEY the narrator templates; a Finding is still produced.

The build guarantee (architecture §5.3, the X3 pin): detection NEVER depends on the
LLM. With no Anthropic key configured:

* :func:`make_narration_client` returns ``None`` (the client is built lazily and
  only with a key);
* the :class:`GroundedNarrator` with a ``None`` client goes straight to the
  deterministic TEMPLATE narrative (``narrative_model=None``, ``reason="no_api_key"``);
* the embedder degrades to the deterministic offline stub (no Voyage key);
* and the full ``analyze_metric`` chain still produces a persisted Finding carrying
  template narrative text with ``narrative_model=None``.

The conftest scrubs the keys for the whole unit suite, so this runs with no key and
no Docker regardless of the developer's shell.
"""

from __future__ import annotations

import pytest

from edis_intelligence.grounding.claude_client import make_narration_client
from edis_intelligence.grounding.embeddings import StubEmbedder, make_embedder
from edis_intelligence.rca.narrator import GroundedNarrator, make_narrator
from edis_intelligence.runner.pipeline import analyze_metric
from edis_intelligence.store.repositories import InMemoryIntelligenceRepo

from edis_l3_testkit import make_demo_reader  # type: ignore[import-not-found]


def test_no_anthropic_key_yields_no_client(no_keys_settings) -> None:
    assert make_narration_client(no_keys_settings) is None


def test_no_voyage_key_yields_stub_embedder(no_keys_settings) -> None:
    emb = make_embedder(no_keys_settings)
    assert isinstance(emb, StubEmbedder)
    assert emb.model == "stub-hash-1024"


@pytest.mark.asyncio
async def test_grounded_narrator_built_from_no_key_settings_templates() -> None:
    """make_narrator(None) -> a GroundedNarrator that always templates with no key."""

    from datetime import datetime, timezone
    from uuid import uuid4

    from edis_contracts.findings import EvidenceBundle, EvidenceItem

    narrator = make_narrator(None)  # no client -> always-template GroundedNarrator
    assert isinstance(narrator, GroundedNarrator)

    bundle = EvidenceBundle(
        bundle_id=uuid4(),
        tenant_id="acme",
        finding_id=uuid4(),
        created_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        items=[
            EvidenceItem(
                kind="metric_window",
                metric_key="revenue",
                dimensions={"region": "EMEA"},
                summary="revenue (region=EMEA) was 61,000 vs an expected 95,000 (-35.8%).",
                values={"observed_value": 61000.0, "expected_value": 95000.0},
            )
        ],
        allowed_numbers=[61000.0, 95000.0, -35.8, 35.8],
    )
    res = await narrator.narrate(bundle)
    assert res.source == "template"
    assert res.narrative_model is None
    assert res.reason == "no_api_key"
    assert res.narrative


@pytest.mark.asyncio
async def test_analyze_metric_with_no_key_narrator_still_produces_finding(no_keys_settings) -> None:
    """End-to-end: no Anthropic client + no Voyage key -> Finding with template narrative."""

    reader = make_demo_reader()
    repo = InMemoryIntelligenceRepo()
    narrator = make_narrator(make_narration_client(no_keys_settings))  # client is None
    embedder = make_embedder(no_keys_settings)  # StubEmbedder

    res = await analyze_metric(
        reader,
        "revenue",
        {"region": "EMEA", "channel": "web"},
        tenant_id="acme",
        narrator=narrator,
        repo=repo,
        embedder=embedder,
    )

    assert res.detected
    assert res.finding is not None
    # template path: text present, model None
    assert res.finding.narrative
    assert res.finding.narrative_model is None
    assert res.narration.source == "template"
    # the finding persisted, readable back, tenant-scoped
    fetched = await repo.get_finding("acme", res.finding.finding_id)
    assert fetched is not None and fetched.narrative_model is None
    # embedding came from the deterministic stub (offline)
    stored = repo.stored_finding(res.finding.finding_id)
    assert stored.embedding_model == "stub-hash-1024"
    assert stored.embedding is not None and len(stored.embedding) == 1024
