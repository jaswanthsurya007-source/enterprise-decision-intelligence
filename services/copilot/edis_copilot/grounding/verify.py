"""The grounding verifier — every numeric claim must trace to a tool result this turn.

THE COPILOT GROUNDING GUARANTEE (the L5 X3 pin) lives here. After the agent produces a
final answer, the verifier:

1. Extracts every numeric token from the answer text (the same pure extractor the L3
   narrator uses — ported, NOT imported, so the copilot has no sibling-service dependency).
2. Asserts each extracted number matches a value returned by a tool *this turn* — the
   flat ``allowed_numbers`` whitelist the agent loop assembled from every
   :class:`~app.tools.base.ToolResult.numbers` it collected — within a small relative
   tolerance (``grounding_rel_tol``).
3. Reports the unmatched numbers so the loop can act: one re-prompt asking the model to
   remove ungrounded figures, then (if still unmatched) STRIP the offending numbers from
   the answer text and lower the confidence.

The extractor is deliberately strict about what counts as a number (digits glued to a
letter/underscore — the ``95`` in ``latency_p95``, the ``api`` run in ``checkout-api`` —
are not numbers; a trailing ``k``/``m``/``b`` rescales only as a standalone magnitude
marker, never the ``m`` in ``min``/``ms``) and lenient about format (``$95,000``,
``-35.8%``, ``1.4k``, ``1,400``). Pure functions only — no SDK, no key, no I/O — so the
guarantee is unit-testable offline and is identical whether the answer came from Opus or
the deterministic offline agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Default relative tolerance when matching a numeric token against an allowed number.
#: Mirrors :attr:`CopilotSettings.grounding_rel_tol` and the L3 narrator's tolerance.
DEFAULT_REL_TOL = 0.02

# Matches signed numbers with optional thousands separators, decimals, a trailing
# %/k/m/b suffix, and an optional leading currency symbol. Captures the numeric core.
# The (?<![A-Za-z0-9_]) lookbehind keeps digits embedded in an identifier (the "95" in
# "latency_p95") from parsing as numbers. A trailing k/m/b suffix only rescales when it
# is a standalone magnitude marker (?![A-Za-z]), never the first letter of "min"/"ms".
# "%" is always a bare suffix that does NOT rescale.
_NUMBER_RE = re.compile(
    r"""
    (?<![A-Za-z0-9_])
    (?P<sign>[-+]?)
    \$?\s?
    (?P<num>
        \d{1,3}(?:,\d{3})+(?:\.\d+)?   # 1,400 / 95,000.5
        | \d+\.\d+                      # 35.8
        | \.\d+                         # .5
        | \d+                           # 1400
    )
    (?:
        (?P<pct>%)
        | (?P<mag>[kKmMbB])(?![A-Za-z])   # 1.4k  — but NOT the 'm' in 'min'/'ms'
    )?
    """,
    re.VERBOSE,
)

_SUFFIX_MULT = {"k": 1e3, "m": 1e6, "b": 1e9}

# Citation markers like ``[1]`` / ``[12]`` are provenance references, NOT data — the
# architecture answer shape uses them inline ("…a 35.8% drop. [2]"). Strip them before
# numeric extraction so a marker integer is never mistaken for an ungrounded figure.
_CITATION_MARKER_RE = re.compile(r"\[\d{1,3}\]")


def _strip_citation_markers(text: str) -> str:
    return _CITATION_MARKER_RE.sub(" ", text)


@dataclass(frozen=True)
class GroundingResult:
    """Outcome of verifying one answer against the per-turn allowed-number whitelist.

    ``ok`` is True when every numeric token in the answer matched an allowed number
    (an answer with no numbers is trivially grounded). ``unmatched`` lists the
    offending values when not. ``extracted`` is every number found (for telemetry).
    """

    ok: bool
    unmatched: tuple[float, ...] = ()
    extracted: tuple[float, ...] = ()


def extract_numbers(text: str) -> list[float]:
    """Extract every numeric token from ``text`` as a list of floats.

    Handles ``$95,000``, ``-35.8%``, ``1.4k``, ``1,400``, ``9``. A ``%`` suffix does NOT
    rescale (``-35.8%`` -> ``-35.8``, matching how tool results store percentages as
    plain numbers); ``k``/``m``/``b`` DO rescale (``1.4k`` -> ``1400``). Pure.
    """

    text = _strip_citation_markers(text)
    out: list[float] = []
    for m in _NUMBER_RE.finditer(text):
        raw = m.group("num").replace(",", "")
        try:
            val = float(raw)
        except ValueError:  # pragma: no cover - regex guarantees a parseable core
            continue
        # A leading '-' is a sign only at a real boundary; between two numbers (a range
        # like "101,917-106,378") it is a separator, so don't negate.
        sign_start = m.start("sign")
        prev = text[sign_start - 1] if sign_start > 0 else ""
        is_range_hyphen = m.group("sign") == "-" and prev.isdigit()
        if m.group("sign") == "-" and not is_range_hyphen:
            val = -val
        mag = m.group("mag")
        if mag and mag.lower() in _SUFFIX_MULT:
            val *= _SUFFIX_MULT[mag.lower()]
        out.append(val)
    return out


def matches_allowed(value: float, allowed: list[float], rel_tol: float) -> bool:
    """True if ``value`` matches any allowed number within relative tolerance.

    Tolerance scales with magnitude (``rel_tol * max(|value|, 1)``) so small absolute
    values still get a sensible window. An exact 0 matches an allowed 0. Matching is
    **sign-insensitive**: a tool result storing ``deviation_pct = -35.8`` grounds an
    answer that says "a 35.8% drop" (the word, not the sign, carries the direction) and
    vice versa — so the guard does not reject a faithful figure on a sign convention.
    """

    tol = rel_tol * max(abs(value), 1.0)
    av = abs(value)
    return any(abs(value - a) <= tol or abs(av - abs(a)) <= tol for a in allowed)


def verify_answer(
    answer: str, allowed_numbers: list[float], *, rel_tol: float = DEFAULT_REL_TOL
) -> GroundingResult:
    """Verify every numeric token in ``answer`` is in ``allowed_numbers``.

    Returns a :class:`GroundingResult`. ``ok`` is True when every extracted number
    matches an allowed number within ``rel_tol`` (an answer with no numbers is trivially
    grounded). Pure — this is the deterministic guard the grounding guarantee rests on.
    """

    extracted = extract_numbers(answer)
    unmatched = [n for n in extracted if not matches_allowed(n, allowed_numbers, rel_tol)]
    return GroundingResult(
        ok=not unmatched,
        unmatched=tuple(unmatched),
        extracted=tuple(extracted),
    )


def strip_ungrounded_numbers(
    answer: str, allowed_numbers: list[float], *, rel_tol: float = DEFAULT_REL_TOL
) -> str:
    """Replace every ungrounded numeric token in ``answer`` with a redaction marker.

    The last-resort enforcement after a failed re-prompt: rather than surface a number
    that does not trace to a tool result, replace the offending token (the matched span,
    including any ``$``/sign/suffix) with ``[unverified]``. Grounded numbers are left
    intact. Idempotent and pure; the result re-verifies clean (modulo the marker, which
    contains no digits).
    """

    def _repl(m: re.Match[str]) -> str:
        # Leave citation markers like ``[1]`` intact (provenance, not data).
        lo, hi = m.start(), m.end()
        if lo > 0 and answer[lo - 1] == "[" and hi < len(answer) and answer[hi] == "]":
            return m.group(0)
        raw = m.group("num").replace(",", "")
        try:
            val = float(raw)
        except ValueError:  # pragma: no cover
            return m.group(0)
        sign_start = m.start("sign")
        prev = answer[sign_start - 1] if sign_start > 0 else ""
        is_range_hyphen = m.group("sign") == "-" and prev.isdigit()
        if m.group("sign") == "-" and not is_range_hyphen:
            val = -val
        mag = m.group("mag")
        if mag and mag.lower() in _SUFFIX_MULT:
            val *= _SUFFIX_MULT[mag.lower()]
        if matches_allowed(val, allowed_numbers, rel_tol):
            return m.group(0)
        return "[unverified]"

    return _NUMBER_RE.sub(_repl, answer)
