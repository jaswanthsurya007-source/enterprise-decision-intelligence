"""Hybrid pgvector + filter search over findings / recommendations.

:class:`HybridSearcher` embeds a query with the (key-guarded) embedder, then runs a
tenant-scoped vector search through the :class:`~app.tools.base.DataPort`. "Hybrid"
here means: vector similarity ranks the candidates, and a ``kinds`` filter (and the
DataPort's own tenant filter) restricts the candidate set — so retrieval combines
semantic ranking with hard structural filters rather than vectors alone.

Everything is tenant-scoped at the DataPort boundary; the searcher never sees another
tenant's docs. Pure-ish: it depends only on the injected embedder + DataPort, so it is
unit-testable with the stub embedder and the InMemory port (no key, no infra).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from edis_copilot.retrieval.embedder import Embedder, embed_query

if TYPE_CHECKING:  # pragma: no cover - typing only; avoids app.tools <-> app.retrieval cycle
    from edis_copilot.tools.base import DataPort


@dataclass(frozen=True)
class RetrievedDoc:
    """One semantically retrieved doc (finding or recommendation), tenant-scoped."""

    kind: str  # "finding" | "recommendation"
    doc_id: str
    score: float
    text: str
    numbers: list[float] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


class HybridSearcher:
    """Embed a query and run tenant-scoped hybrid pgvector search via the DataPort."""

    def __init__(self, data: "DataPort", embedder: Embedder) -> None:
        self._data = data
        self._embedder = embedder

    @property
    def embedding_model(self) -> str:
        """Provenance of the query embedding (``voyage-3`` or ``stub-hash-1024``)."""

        return self._embedder.model

    async def search(
        self,
        tenant_id: str,
        query: str,
        *,
        kinds: list[str] | None = None,
        limit: int = 8,
    ) -> list[RetrievedDoc]:
        """Return up to ``limit`` retrieved docs for ``query`` within ``tenant_id``.

        ``kinds`` restricts to a subset of {"finding", "recommendation"} (the hybrid
        structural filter). ``tenant_id`` is passed straight to the DataPort, which
        enforces tenant isolation.
        """

        vector, _model = embed_query(self._embedder, query)
        rows = await self._data.vector_search(tenant_id, vector, kinds=kinds, limit=limit)
        return [
            RetrievedDoc(
                kind=str(r.get("kind", "")),
                doc_id=str(r.get("id", "")),
                score=float(r.get("score", 0.0)),
                text=str(r.get("text", "")),
                numbers=[float(n) for n in r.get("numbers", []) if isinstance(n, (int, float))],
                payload=dict(r.get("payload", {})),
            )
            for r in rows
        ]
