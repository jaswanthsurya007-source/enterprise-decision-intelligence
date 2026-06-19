"""P3 — the no-key stub embedder still ranks the relevant finding first.

With no Voyage key the copilot embeds queries with the deterministic hashed-token stub
that MIRRORS the L3 corpus embedder. Because the demo corpus in ``conftest`` was indexed
with that same stub, semantic retrieval is meaningful offline: an on-topic query lands
near the on-topic finding in the shared vector space and ranks it first. These tests prove
retrieval works end-to-end (embedder -> HybridSearcher -> DataPort -> tool) with no key
and no infra.
"""

from __future__ import annotations

from edis_copilot.retrieval.embedder import (
    STUB_MODEL,
    StubEmbedder,
    embed_query,
    make_embedder,
    stub_embedding,
)
from edis_copilot.retrieval.search import HybridSearcher


def test_stub_embedder_is_deterministic_and_unit_norm():
    """Same text -> identical vector; the vector is L2-normalized (or zero for blanks)."""

    a = stub_embedding("EMEA revenue dropped")
    b = stub_embedding("EMEA revenue dropped")
    assert a == b
    assert len(a) == 1024
    norm = sum(x * x for x in a) ** 0.5
    assert abs(norm - 1.0) < 1e-9
    assert stub_embedding("") == [0.0] * 1024  # blank -> zero vector, never raises


def test_make_embedder_falls_back_to_stub_without_key(settings):
    """With no VOYAGE_API_KEY, the selector returns the deterministic stub embedder."""

    emb = make_embedder(settings)
    assert isinstance(emb, StubEmbedder)
    assert emb.model == STUB_MODEL
    vec, model = embed_query(emb, "why did revenue drop")
    assert model == STUB_MODEL and len(vec) == 1024


async def test_stub_search_ranks_on_topic_finding_first(data):
    """An on-topic query ranks the EMEA revenue finding above the unrelated APAC doc."""

    searcher = HybridSearcher(data, StubEmbedder())
    docs = await searcher.search("acme", "why did EMEA web revenue drop", limit=5)
    assert docs, "expected at least one retrieved doc"
    assert docs[0].doc_id == "f-7a3"  # the relevant finding, ranked first
    # Scores are descending (the relevant doc out-scores the off-topic APAC growth doc).
    scores = [d.score for d in docs]
    assert scores == sorted(scores, reverse=True)
    apac = next((d for d in docs if d.doc_id == "f-apac"), None)
    if apac is not None:
        assert docs[0].score > apac.score


async def test_stub_search_carries_numbers_for_grounding(data):
    """The retrieved relevant doc carries the real numbers (the grounding whitelist seed)."""

    searcher = HybridSearcher(data, StubEmbedder())
    docs = await searcher.search("acme", "EMEA revenue drop", limit=3)
    top = docs[0]
    assert any(abs(n - 61000.0) < 1.0 for n in top.numbers)


async def test_stub_search_is_tenant_scoped(data):
    """The stub query for acme never retrieves globex's identically-embedded doc."""

    searcher = HybridSearcher(data, StubEmbedder())
    docs = await searcher.search("acme", "EMEA revenue drop", limit=10)
    assert "f-globex" not in {d.doc_id for d in docs}
