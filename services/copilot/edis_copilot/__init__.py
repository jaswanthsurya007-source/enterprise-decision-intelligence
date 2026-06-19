"""EDIS L5 AI Copilot service (``edis_copilot`` distribution, import name ``app``).

P1 — the copilot *data/tool layer*: a frozen, deterministic registry of strictly
read-only, tenant-scoped tools; voyage-3 + pgvector retrieval with a token-budget
packer; a lazy, key-guarded Claude client seam (with model constants); the FROZEN
cached system prompt; and a :class:`~app.tools.base.DataPort` Protocol with an
InMemory fake so every tool is unit-testable with no infrastructure and no API keys.

The agent loop, grounding verifier, and SSE chat API are P2 — they import this layer.

Nothing in this package connects to a live broker, database, or model provider at
import time; importing the service is always safe in CI with no Docker and no keys.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
