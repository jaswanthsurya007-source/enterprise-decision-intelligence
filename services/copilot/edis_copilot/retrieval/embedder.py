"""Voyage ``voyage-3`` query embeddings with a deterministic offline stub fallback.

This MIRRORS the L3 grounding embedder
(:mod:`edis_intelligence.grounding.embeddings`) so the copilot embeds a query exactly
the way the corpus was embedded — same 1024-dim width, same deterministic hashed-token
stub when no key is present, same model-string provenance (``"voyage-3"`` vs
``"stub-hash-1024"``). Matching the corpus embedding is what makes the offline stub
retrieval meaningful: a finding indexed by the L3 stub and a query embedded by this
stub land in the same vector space.

Per the verified rules, real embeddings use the SEPARATE ``voyageai`` package (NOT
``anthropic``), use ``input_type="query"`` at copilot query time, and the SDK is
imported lazily so importing this module needs neither the SDK nor a key.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import TYPE_CHECKING, Protocol

from edis_platform.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from edis_platform.settings import Settings

_log = get_logger(__name__)

#: Embedding width — matches the L3 embedder, CopilotSettings.embedding_dim, and the
#: L2 migration's ``vector(1024)`` column. The query and corpus MUST share this width.
EMBEDDING_DIM = 1024
VOYAGE_MODEL = "voyage-3"
STUB_MODEL = "stub-hash-1024"

_TOKEN_RE = re.compile(r"[A-Za-z0-9_.]+")


class Embedder(Protocol):
    """Structural protocol every embedder satisfies (mirrors the L3 Embedder)."""

    model: str
    dim: int

    def embed(self, text: str, *, input_type: str = "query") -> list[float]: ...


# ---------------------------------------------------------------------------
# Deterministic offline stub (byte-for-byte identical to the L3 stub)
# ---------------------------------------------------------------------------
def stub_embedding(text: str, *, dim: int = EMBEDDING_DIM) -> list[float]:
    """Deterministic hashed-token embedding, L2-normalized to a unit vector.

    Identical to :func:`edis_intelligence.grounding.embeddings.stub_embedding` so a
    finding the L3 stub indexed and a query this stub embeds occupy the same space.
    Each sha1-hashed token contributes a signed unit to a bucket; the vector is then
    L2-normalized. Never raises; blank text yields a zero vector.
    """

    vec = [0.0] * dim
    tokens = _TOKEN_RE.findall(text.lower())
    if not tokens:
        return vec
    for tok in tokens:
        h = hashlib.sha1(tok.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "big") % dim
        sign = 1.0 if (h[4] & 1) else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0.0:
        vec = [v / norm for v in vec]
    return vec


class StubEmbedder:
    """Offline, deterministic embedder used whenever no Voyage key is present."""

    model = STUB_MODEL

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def embed(self, text: str, *, input_type: str = "query") -> list[float]:
        return stub_embedding(text, dim=self.dim)


# ---------------------------------------------------------------------------
# Voyage voyage-3 (real path)
# ---------------------------------------------------------------------------
class VoyageEmbedder:
    """Real ``voyage-3`` embedder; constructed only when ``VOYAGE_API_KEY`` is set."""

    model = VOYAGE_MODEL

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        import voyageai  # lazy: SDK not needed to import this module

        self._client = voyageai.Client()  # reads VOYAGE_API_KEY from the env
        self.dim = dim
        self._fallback = StubEmbedder(dim)

    def embed(self, text: str, *, input_type: str = "query") -> list[float]:
        """Embed ``text`` with voyage-3; degrade to the stub on any error.

        ``input_type`` is ``"query"`` at copilot query time (the corpus was embedded
        with ``"document"`` by L3). A transient API failure falls back to the
        deterministic stub so retrieval never hard-fails on a flaky embedding call.
        """

        try:
            res = self._client.embed([text], model=VOYAGE_MODEL, input_type=input_type)
            return list(res.embeddings[0])
        except Exception as exc:  # noqa: BLE001 - never block retrieval on embeddings
            _log.warning(
                "voyage query embed failed; using stub embedding",
                extra={"error": str(exc)},
            )
            return self._fallback.embed(text, input_type=input_type)


def make_embedder(settings: "Settings", *, dim: int = EMBEDDING_DIM) -> Embedder:
    """Return a real :class:`VoyageEmbedder` iff ``VOYAGE_API_KEY`` is set, else the stub.

    Mirrors the L3 selector: with a key we use voyage-3; without one we degrade to the
    deterministic offline stub so query embedding works with no infrastructure and no
    key, and the whole retrieval chain is unit-testable.
    """

    api_key = getattr(settings, "voyage_api_key", None)
    if not api_key:
        return StubEmbedder(dim)
    try:
        return VoyageEmbedder(dim)
    except Exception as exc:  # noqa: BLE001 - SDK missing / construction failure
        _log.warning(
            "could not build Voyage client; using stub query embeddings",
            extra={"error": str(exc)},
        )
        return StubEmbedder(dim)


def embed_query(embedder: Embedder, text: str) -> tuple[list[float], str]:
    """Embed a copilot ``text`` query, returning ``(vector, embedding_model)``.

    Always uses ``input_type="query"`` (the copilot side of the L3 document/query
    split). The returned model string is the provenance to record on the retrieval.
    """

    return embedder.embed(text, input_type="query"), embedder.model
