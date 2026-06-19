"""Copilot retrieval: voyage-3 query embedding, hybrid pgvector search, token packer.

* :mod:`edis_copilot.retrieval.embedder` mirrors the L3 grounding embedder — real ``voyage-3``
  when ``VOYAGE_API_KEY`` is set, a deterministic offline stub otherwise — so the
  copilot embeds queries with no key and stays unit-testable.
* :mod:`edis_copilot.retrieval.search` runs hybrid pgvector + filter search over findings /
  recommendations through the :class:`~app.tools.base.DataPort`.
* :mod:`edis_copilot.retrieval.packer` trims combined tool/retrieval results to a token budget
  before they enter the model context.
"""

from __future__ import annotations

from edis_copilot.retrieval.embedder import (
    EMBEDDING_DIM,
    STUB_MODEL,
    VOYAGE_MODEL,
    Embedder,
    StubEmbedder,
    VoyageEmbedder,
    embed_query,
    make_embedder,
)
from edis_copilot.retrieval.packer import PackedResults, pack_results
from edis_copilot.retrieval.search import HybridSearcher, RetrievedDoc

__all__ = [
    "EMBEDDING_DIM",
    "STUB_MODEL",
    "VOYAGE_MODEL",
    "Embedder",
    "StubEmbedder",
    "VoyageEmbedder",
    "embed_query",
    "make_embedder",
    "HybridSearcher",
    "RetrievedDoc",
    "PackedResults",
    "pack_results",
]
