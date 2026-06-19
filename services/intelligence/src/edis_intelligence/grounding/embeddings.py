"""Voyage ``voyage-3`` embeddings with a deterministic offline stub fallback.

Findings are embedded so the copilot (L5) can semantically retrieve them from the
pgvector ``embedding`` column. Per the verified rules, real embeddings use the
SEPARATE ``voyageai`` package (NOT ``anthropic``)::

    import voyageai
    vo = voyageai.Client()                      # reads VOYAGE_API_KEY
    vo.embed([text], model="voyage-3", input_type="document").embeddings[0]  # 1024-dim

Use ``input_type="document"`` when indexing a finding and ``input_type="query"`` at
copilot query time.

When ``VOYAGE_API_KEY`` is absent (CI, local dev, unit tests) the embedder DEGRADES
to a deterministic local stub: a hashed-token bag-of-features projected into a fixed
1024-dim vector and L2-normalized. pgvector still receives a real vector, retrieval
still functions (coarsely), and tests run fully offline. ``embedding_model`` is
recorded accordingly (``"voyage-3"`` vs ``"stub-hash-1024"``) so provenance is never
ambiguous.

The Voyage SDK is imported lazily inside the real path, so importing this module
needs neither the SDK nor a key.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import TYPE_CHECKING, Protocol

from edis_platform.logging import get_logger

if TYPE_CHECKING:
    from edis_platform.settings import Settings

_log = get_logger(__name__)

#: Embedding width — matches IntelligenceSettings.embedding_dim and the migration's
#: ``vector(1024)`` column.
EMBEDDING_DIM = 1024
VOYAGE_MODEL = "voyage-3"
STUB_MODEL = "stub-hash-1024"

_TOKEN_RE = re.compile(r"[A-Za-z0-9_.]+")


class Embedder(Protocol):
    """Structural protocol every embedder satisfies."""

    #: The embedding model string recorded on the finding (provenance).
    model: str
    dim: int

    def embed(self, text: str, *, input_type: str = "document") -> list[float]: ...


# ---------------------------------------------------------------------------
# Deterministic offline stub
# ---------------------------------------------------------------------------
def stub_embedding(text: str, *, dim: int = EMBEDDING_DIM) -> list[float]:
    """Deterministic hashed-token embedding, L2-normalized to a unit vector.

    Each token is hashed (sha1) into a bucket index and a sign, and its (tf-weighted)
    contribution is accumulated; the resulting vector is L2-normalized. Identical text
    always yields the identical vector — so it is a stable, offline stand-in for a real
    embedding (good enough for the copilot to retrieve obviously-similar findings, and
    fully testable without a key). Never raises; an empty/blank text yields a zero
    vector.
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

    def embed(self, text: str, *, input_type: str = "document") -> list[float]:
        return stub_embedding(text, dim=self.dim)


# ---------------------------------------------------------------------------
# Voyage voyage-3 (real path)
# ---------------------------------------------------------------------------
class VoyageEmbedder:
    """Real ``voyage-3`` embedder; constructed only when ``VOYAGE_API_KEY`` is set."""

    model = VOYAGE_MODEL

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        import voyageai

        self._client = voyageai.Client()  # reads VOYAGE_API_KEY from the env
        self.dim = dim
        self._fallback = StubEmbedder(dim)

    def embed(self, text: str, *, input_type: str = "document") -> list[float]:
        """Embed ``text`` with voyage-3; degrade to the stub on any error.

        ``input_type`` is ``"document"`` for indexing and ``"query"`` at query time
        (the build rule). A transient API failure falls back to the deterministic
        stub so a finding still gets *a* vector and persistence never blocks.
        """

        try:
            res = self._client.embed([text], model=VOYAGE_MODEL, input_type=input_type)
            return list(res.embeddings[0])
        except Exception as exc:  # noqa: BLE001 - never block persistence on embeddings
            _log.warning(
                "voyage embed failed; using stub embedding",
                extra={"error": str(exc)},
            )
            return self._fallback.embed(text, input_type=input_type)


def make_embedder(settings: "Settings", *, dim: int = EMBEDDING_DIM) -> Embedder:
    """Return a real :class:`VoyageEmbedder` iff ``VOYAGE_API_KEY`` is set, else the stub.

    The selection mirrors the narrator's lazy/no-key handling: with a key we use
    voyage-3; without one we degrade to the deterministic offline stub so pgvector
    still receives a vector and the whole chain is testable with no infrastructure.
    """

    api_key = getattr(settings, "voyage_api_key", None)
    if not api_key:
        return StubEmbedder(dim)
    try:
        return VoyageEmbedder(dim)
    except Exception as exc:  # noqa: BLE001 - SDK missing / construction failure
        _log.warning(
            "could not build Voyage client; using stub embeddings",
            extra={"error": str(exc)},
        )
        return StubEmbedder(dim)


def embed_text(
    embedder: Embedder, text: str, *, input_type: str = "document"
) -> tuple[list[float], str]:
    """Embed ``text`` and return ``(vector, embedding_model)`` for persistence.

    The returned model string is exactly what should be recorded as the finding's
    ``embedding_model`` provenance (``"voyage-3"`` or ``"stub-hash-1024"``).
    """

    return embedder.embed(text, input_type=input_type), embedder.model
