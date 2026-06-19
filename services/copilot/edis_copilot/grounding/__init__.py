"""Copilot grounding: the numeric-claim verifier + citation builder.

* :mod:`edis_copilot.grounding.verify` — the ported (not imported) pure numeric extractor +
  verifier the L3 narrator uses, plus :func:`strip_ungrounded_numbers`. Every number in
  a copilot answer must trace to a value a tool returned THIS turn.
* :mod:`edis_copilot.grounding.citations` — turns the per-turn tool results into numbered
  citations + the ``facts_used`` whitelist the UI renders as authoritative.

Pure functions only — no SDK, no key, no I/O — so the grounding guarantee is identical
whether the answer came from Opus or the deterministic offline agent.
"""

from __future__ import annotations

from edis_copilot.grounding.citations import (
    Citation,
    CitationSet,
    allowed_numbers,
    build_citations,
)
from edis_copilot.grounding.verify import (
    DEFAULT_REL_TOL,
    GroundingResult,
    extract_numbers,
    matches_allowed,
    strip_ungrounded_numbers,
    verify_answer,
)

__all__ = [
    "DEFAULT_REL_TOL",
    "Citation",
    "CitationSet",
    "GroundingResult",
    "allowed_numbers",
    "build_citations",
    "extract_numbers",
    "matches_allowed",
    "strip_ungrounded_numbers",
    "verify_answer",
]
