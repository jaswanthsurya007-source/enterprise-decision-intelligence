"""Claude model constants and the verified call-shape for the copilot.

Per the verified Claude-API rules and the EDIS mandate:

* ``MODEL_OPUS = "claude-opus-4-8"`` — the agentic reasoning / synthesis model. Runs
  with **adaptive thinking + summarized display + effort=high**, streamed at a large
  ``max_tokens`` (~64K). NO ``temperature`` / ``top_p`` / ``top_k`` / ``budget_tokens``
  (all 400 on opus-4-8). ``effort`` is set on the OPUS call only.
* ``MODEL_HAIKU = "claude-haiku-4-5"`` — cheap structured routing / intent
  classification. Haiku does NOT accept the ``effort`` parameter and is used purely for
  structured outputs; the routing call shape lives with the P2 router.

These are plain constants/dicts — importing this module needs no SDK and no key. The
P2 agent loop reads them to build the Opus request; defining them here keeps the call
shape in one verified place.
"""

from __future__ import annotations

from typing import Any

#: Agentic reasoning / synthesis model (1M ctx, 128K max output, adaptive thinking).
MODEL_OPUS = "claude-opus-4-8"
#: Cheap routing / classification model (structured outputs; no effort param).
MODEL_HAIKU = "claude-haiku-4-5"

#: Adaptive thinking with a summarized display so the UI can show a reasoning trace
#: (display set explicitly — the default is "omitted" on opus-4-8).
OPUS_THINKING: dict[str, str] = {"type": "adaptive", "display": "summarized"}

#: Effort for the Opus synthesis loop (high — intelligence-sensitive grounded RCA).
OPUS_EFFORT = "high"

#: Streamed max_tokens for the Opus synthesis call (build-spec value).
OPUS_MAX_TOKENS = 64000


def opus_request_kwargs(*, max_tokens: int = OPUS_MAX_TOKENS) -> dict[str, Any]:
    """Return the non-message kwargs for an ``messages.stream`` Opus call.

    The verified shape: model, max_tokens, adaptive thinking (summarized), and
    ``output_config={"effort": "high"}``. No sampling params, no budget_tokens. The
    caller adds ``system=`` (the cached blocks), ``tools=``, and ``messages=``. P2 uses
    this so the request shape stays in one verified place.
    """

    return {
        "model": MODEL_OPUS,
        "max_tokens": max_tokens,
        "thinking": dict(OPUS_THINKING),
        "output_config": {"effort": OPUS_EFFORT},
    }
