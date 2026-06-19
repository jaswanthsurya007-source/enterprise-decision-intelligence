"""Trim combined tool/retrieval results to a token budget before model context.

The agent loop (P2) accumulates :class:`~app.tools.base.ToolResult` rows across tool
calls; before feeding them back to Opus they must fit a budget so the context stays
bounded and the cached prefix dominates. :func:`pack_results` greedily keeps whole
rows in order until the running token estimate would exceed the budget, then stops and
records how many rows were dropped.

Token estimation is a deterministic, offline ``len(json) / chars_per_token`` heuristic
— no tokenizer and no API key required, so packing is unit-testable. (P2 may refine
with ``messages.count_tokens`` when a key is present; correctness never depends on the
exact count — the budget is a soft ceiling.) Crucially, the packer NEVER edits a row's
values: it keeps a row whole or drops it, so it can never fabricate or truncate a
number that the grounding verifier later checks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PackedResults:
    """The packer output: kept rows plus what was trimmed and the size estimate."""

    rows: list[dict[str, Any]]
    kept: int
    dropped: int
    estimated_tokens: int
    truncated: bool = False

    @property
    def trimmed(self) -> bool:
        """True if any rows were dropped to fit the budget."""

        return self.dropped > 0


def estimate_tokens(obj: Any, *, chars_per_token: int = 4) -> int:
    """Estimate tokens for ``obj`` as ``len(compact_json) / chars_per_token``.

    Deterministic and offline. Uses sorted keys + compact separators so the estimate
    is stable across runs (and never perturbs prompt caching). ``chars_per_token`` is
    a coarse 4-by-default heuristic; a higher value estimates fewer tokens.
    """

    cpt = max(1, int(chars_per_token))
    text = json.dumps(obj, default=str, sort_keys=True, separators=(",", ":"))
    return max(1, (len(text) + cpt - 1) // cpt)


def pack_results(
    rows: list[dict[str, Any]],
    *,
    token_budget: int = 6000,
    chars_per_token: int = 4,
) -> PackedResults:
    """Greedily keep whole rows (in order) until the token budget is reached.

    Rows are kept intact — a row is included only if adding its full estimated size
    keeps the running total within ``token_budget``; otherwise it (and the rest) are
    dropped and ``truncated`` is set. The first row is always kept even if it alone
    exceeds the budget (so the model never gets an empty result when there is data),
    which is the only case where the estimate may exceed the budget.
    """

    kept: list[dict[str, Any]] = []
    running = 0
    truncated = False
    for i, row in enumerate(rows):
        cost = estimate_tokens(row, chars_per_token=chars_per_token)
        if i == 0:
            kept.append(row)
            running += cost
            if cost > token_budget:
                truncated = True
            continue
        if running + cost > token_budget:
            truncated = True
            break
        kept.append(row)
        running += cost
    return PackedResults(
        rows=kept,
        kept=len(kept),
        dropped=len(rows) - len(kept),
        estimated_tokens=running,
        truncated=truncated,
    )
