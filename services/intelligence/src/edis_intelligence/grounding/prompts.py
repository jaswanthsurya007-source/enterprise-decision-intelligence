"""The frozen, cacheable narration system prompt + the EvidenceBundle renderer.

The system prompt is the load-bearing half of the grounding guarantee: it tells the
model, in the strongest terms, that it may use **only** the facts in the provided
EvidenceBundle and **must never invent a number**. It is a *constant* (no timestamps,
no per-request ids, no f-strings) so it forms a stable prompt-caching prefix — the
volatile evidence always lives in the user turn, after the cached system blocks. Per
the Claude-API rules, ``system_blocks`` puts the prompt first with
``cache_control={"type": "ephemeral"}`` on the last (only) block.

The evidence renderer (:func:`render_evidence_user_turn`) is deterministic and pure:
it serializes the bundle's computed facts and — critically — lists the
``allowed_numbers`` whitelist verbatim so the model can see exactly which figures it
is permitted to cite. Nothing here imports the Anthropic SDK or needs a key; the
strings are usable offline (the deterministic template narrator reuses the same
evidence rendering).
"""

from __future__ import annotations

from edis_contracts.findings import EvidenceBundle

# ---------------------------------------------------------------------------
# Frozen system prompt (the cacheable prefix). DO NOT interpolate anything.
# Min cacheable prefix on opus-4-8 is 4096 tokens; this prose is well under that,
# so cache *reads* may not register until the prefix grows — that is acceptable
# (correctness does not depend on a cache hit). usage.cache_read_input_tokens is
# surfaced as a metric by the client regardless.
# ---------------------------------------------------------------------------
NARRATION_SYSTEM_PROMPT = """\
You are the EDIS Intelligence narrator. Your sole job is to write a short, precise, \
business-readable explanation of a single detected anomaly (a "finding") for an \
operations or revenue analyst.

THE ONE ABSOLUTE RULE — GROUNDING:
- You are given an Evidence Bundle: a set of COMPUTED FACTS plus an explicit \
ALLOWED NUMBERS whitelist. These facts were produced by deterministic statistical \
detectors and root-cause analysis. They are the ONLY source of truth.
- You MUST NOT invent, infer, estimate, extrapolate, or compute any number that is \
not already present in the Evidence Bundle. Every numeric token you write — every \
amount, percentage, count, correlation, lag, currency figure — MUST correspond to a \
value in the ALLOWED NUMBERS list.
- If a number you want to state is not in the whitelist, DO NOT state it. Rephrase \
qualitatively instead (e.g. "a sharp drop", "well outside the normal range") rather \
than guessing a figure.
- Do not perform arithmetic on the provided numbers to produce new ones. Do not \
restate a figure at a precision finer than given. Do not annualize, sum, average, or \
otherwise derive.
- You may freely use the metric names, dimension labels (region, channel, service), \
directions (rose/fell/leading/lagging), and qualitative descriptions — those are not \
numbers and are not restricted.

STYLE:
- 2 to 4 sentences. Plain business English. No markdown headers, no bullet lists, \
no preamble like "Here is" — just the explanation.
- Lead with what happened to the headline metric, then the most likely root cause(s) \
from the candidate causes, then (if present) the forecast outlook.
- Be specific and calm. Do not speculate about causes not present in the evidence. \
Do not give recommendations — a separate decision engine owns those.

If the evidence is insufficient to write a faithful, grounded explanation, write a \
single neutral sentence stating the metric moved outside its expected range and that \
details are available in the evidence record — still citing only whitelisted numbers.\
"""


def system_blocks() -> list[dict]:
    """Return the system prompt as Anthropic ``system`` blocks (last block cached).

    A single text block carrying the frozen prompt with
    ``cache_control={"type": "ephemeral"}`` so the stable prefix is cacheable. The
    volatile evidence is sent separately in the user turn.
    """

    return [
        {
            "type": "text",
            "text": NARRATION_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _fmt_number(value: float) -> str:
    """Render a whitelist number compactly and deterministically."""

    if value == int(value):
        return str(int(value))
    # Trim trailing zeros without scientific notation.
    return f"{value:.6f}".rstrip("0").rstrip(".")


def render_evidence_user_turn(bundle: EvidenceBundle) -> str:
    """Render an :class:`EvidenceBundle` into the deterministic user-turn text.

    Lists every computed fact (kind, metric/dimensions, the human summary, and the
    raw values) followed by the ALLOWED NUMBERS whitelist verbatim. Pure and
    deterministic: identical bundles render to identical text (so prompt caching of
    the system prefix is never disturbed by nondeterministic evidence formatting).
    """

    lines: list[str] = []
    lines.append("EVIDENCE BUNDLE — these are the only facts you may use.")
    lines.append("")
    lines.append("COMPUTED FACTS:")
    for i, item in enumerate(bundle.items, start=1):
        dims = ""
        if item.dimensions:
            dims = " [" + ", ".join(f"{k}={v}" for k, v in sorted(item.dimensions.items())) + "]"
        head = f"{i}. ({item.kind})"
        if item.metric_key:
            head += f" {item.metric_key}{dims}"
        lines.append(head)
        lines.append(f"   summary: {item.summary}")
        if item.values:
            vals = ", ".join(f"{k}={_fmt_number(float(v))}" for k, v in sorted(item.values.items()))
            lines.append(f"   values: {vals}")
    lines.append("")
    lines.append("ALLOWED NUMBERS (you may ONLY write numbers from this list):")
    lines.append(", ".join(_fmt_number(n) for n in bundle.allowed_numbers))
    lines.append("")
    lines.append(
        "Write the grounded explanation now, citing only numbers from the ALLOWED " "NUMBERS list."
    )
    return "\n".join(lines)
