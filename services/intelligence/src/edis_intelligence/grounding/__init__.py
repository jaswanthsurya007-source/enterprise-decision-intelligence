"""The LLM + embeddings layer for L3 (X3).

Three collaborators, all built to degrade safely with no API key:

* :mod:`~edis_intelligence.grounding.prompts` — the frozen, cacheable system prompt
  that enforces the grounding rule ("use ONLY the provided facts; never invent
  numbers") plus the deterministic user-turn renderer of an EvidenceBundle.
* :mod:`~edis_intelligence.grounding.claude_client` — a thin async wrapper over
  ``anthropic.AsyncAnthropic`` built **lazily and only when an API key is present**,
  applying the verified Claude-API rules (streaming + adaptive thinking + effort,
  prompt caching, stop-reason handling). When no key is set the wrapper is simply
  never constructed and the narrator takes the deterministic template path.
* :mod:`~edis_intelligence.grounding.embeddings` — a Voyage ``voyage-3`` embedder
  with a deterministic offline stub fallback (hashed-token features -> 1024-dim
  L2-normalized vector) so pgvector always receives a vector and tests run with no
  key.

THE GROUNDING GUARANTEE: the narrator (``rca/narrator.py``) hands the model ONLY the
EvidenceBundle; a verifier then extracts every numeric token from the narrative and
asserts each matches a value in ``EvidenceBundle.allowed_numbers`` within tolerance.
On any failure / refusal / API error / missing key the LLM text is discarded and a
deterministic template narrative is emitted instead. Detection never depends on the
LLM.
"""

from __future__ import annotations

from edis_intelligence.grounding.claude_client import (
    ClaudeNarrationClient,
    NarrationOutcome,
    make_narration_client,
)
from edis_intelligence.grounding.embeddings import (
    Embedder,
    VoyageEmbedder,
    embed_text,
    make_embedder,
    stub_embedding,
)
from edis_intelligence.grounding.prompts import (
    NARRATION_SYSTEM_PROMPT,
    render_evidence_user_turn,
    system_blocks,
)

__all__ = [
    "ClaudeNarrationClient",
    "NarrationOutcome",
    "make_narration_client",
    "Embedder",
    "VoyageEmbedder",
    "embed_text",
    "make_embedder",
    "stub_embedding",
    "NARRATION_SYSTEM_PROMPT",
    "render_evidence_user_turn",
    "system_blocks",
]
