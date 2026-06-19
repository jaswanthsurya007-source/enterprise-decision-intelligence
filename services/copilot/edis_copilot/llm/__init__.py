"""The copilot LLM seam: model constants, the lazy client factory, the frozen prompt.

* :mod:`edis_copilot.llm.models` — model id constants (``MODEL_OPUS``, ``MODEL_HAIKU``) and
  call-shape defaults (verified ``claude-opus-4-8`` rules: adaptive thinking,
  ``effort=high``, no temperature/top_p/budget_tokens).
* :mod:`edis_copilot.llm.client` — a lazy, key-guarded :class:`AsyncAnthropic` factory that
  returns ``None`` with no key, so the copilot runs fully offline.
* :mod:`edis_copilot.llm.prompts` — the FROZEN, cacheable system prompt that pins the grounding
  guarantee ("answer ONLY from tool results; never invent numbers; cite every figure").
"""

from __future__ import annotations

from edis_copilot.llm.client import make_anthropic_client
from edis_copilot.llm.models import (
    MODEL_HAIKU,
    MODEL_OPUS,
    OPUS_EFFORT,
    OPUS_MAX_TOKENS,
    OPUS_THINKING,
)
from edis_copilot.llm.prompts import COPILOT_SYSTEM_PROMPT, system_blocks

__all__ = [
    "MODEL_OPUS",
    "MODEL_HAIKU",
    "OPUS_EFFORT",
    "OPUS_MAX_TOKENS",
    "OPUS_THINKING",
    "make_anthropic_client",
    "COPILOT_SYSTEM_PROMPT",
    "system_blocks",
]
