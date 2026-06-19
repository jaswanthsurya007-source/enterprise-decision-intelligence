"""The grounded narrator — Claude over the EvidenceBundle, with a hard grounding guard.

THE GROUNDING GUARANTEE (X3 pin) lives here. The narrator:

1. Builds the prompt from an :class:`~edis_contracts.findings.EvidenceBundle` (the LLM
   sees nothing else) and calls Claude (opus-4-8) via the lazy client.
2. Runs the **grounding verifier**: extract every numeric token from the returned
   narrative and assert each matches a value in ``EvidenceBundle.allowed_numbers``
   within a small relative tolerance (``grounding_rel_tol``).
3. On ANY of — an unmatched number, a refusal, an API error, a missing key, or an
   empty narrative — DISCARD the LLM text and emit a **deterministic TEMPLATE
   narrative** built from the same evidence, with ``narrative_model=None``.

So ``narrative_model`` is set to the LLM model **only** when an LLM narrative passed
the guard; the template path always reports ``narrative_model=None``. Detection never
depends on the LLM, and findings always carry *a* narrative (LLM or template). A
finding may also legitimately carry ``narrative=None`` if a caller chooses to skip
narration entirely — but the default narrators here always produce template text.

The number extractor is deliberately strict about what counts as a number (it ignores
years embedded in ISO dates, ordinals, and the like is not a concern because the
prompt forbids dates) and lenient about format (``$95,000``, ``-35.8%``, ``1.4k``,
``1,400``). Anything that parses as a real number must be in the whitelist.

A :class:`FakeNarrator` and the :class:`Narrator` protocol let X4 unit-test the whole
analyze chain with no API key and no infrastructure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from edis_contracts.findings import EvidenceBundle
from edis_platform.logging import get_logger

from edis_intelligence.grounding.claude_client import ClaudeNarrationClient

_log = get_logger(__name__)

#: Default relative tolerance the guard allows when matching a numeric token against
#: an allowed number (mirrors IntelligenceSettings.grounding_rel_tol).
DEFAULT_REL_TOL = 0.02


@dataclass(frozen=True)
class NarrationResult:
    """The outcome of narrating one finding.

    ``narrative`` is always populated (LLM text if it passed the guard, otherwise the
    deterministic template). ``narrative_model`` is the LLM model **iff** the LLM
    narrative passed the guard, else ``None``. ``source`` is ``"llm"`` or
    ``"template"``; ``reason`` explains why the template was used (when applicable).
    """

    narrative: str
    narrative_model: str | None
    source: str  # "llm" | "template"
    reason: str | None = None
    #: Numbers extracted from the LLM narrative that were NOT in the whitelist
    #: (populated only when an LLM narrative was rejected for ungrounded numbers).
    unmatched_numbers: tuple[float, ...] = ()
    cache_read_input_tokens: int = 0


# ---------------------------------------------------------------------------
# Numeric-token extraction + grounding verification (pure)
# ---------------------------------------------------------------------------
# Matches signed numbers with optional thousands separators, decimals, a trailing
# %/k/m/b suffix, and an optional leading currency symbol. Captures the numeric core.
# A trailing k/m/b suffix only rescales when it is a *standalone* magnitude marker
# (end of token), never when it is the first letter of a following word like "min" or
# "ms" — otherwise "1440 min" would parse as 1.44e9. The negative lookahead
# (?![A-Za-z]) enforces that. "%" is always a bare suffix.
# Digits embedded in an identifier (the "95" in "latency_p95", the "api" run in
# "checkout-api") are NOT numbers — the (?<![A-Za-z_]) lookbehind requires the number
# not be glued to a letter/underscore on its left. A trailing k/m/b suffix only
# rescales when it is a standalone magnitude marker (?![A-Za-z]), never the first
# letter of "min"/"ms". "%" is always a bare suffix.
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


def extract_numbers(text: str) -> list[float]:
    """Extract every numeric token from ``text`` as a list of floats.

    Handles ``$95,000``, ``-35.8%``, ``1.4k``, ``1,400``, ``9``. A ``%`` suffix does
    NOT rescale (``-35.8%`` -> ``-35.8``, matching how the evidence stores percentages
    as plain numbers); ``k``/``m``/``b`` DO rescale (``1.4k`` -> ``1400``). Pure.
    """

    out: list[float] = []
    for m in _NUMBER_RE.finditer(text):
        raw = m.group("num").replace(",", "")
        try:
            val = float(raw)
        except ValueError:
            continue
        # A leading '-' is a sign only at a real boundary; between two numbers (a
        # range like "101,917-106,378") it is a separator, so don't negate.
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


def _matches_allowed(value: float, allowed: list[float], rel_tol: float) -> bool:
    """True if ``value`` matches any allowed number within relative tolerance.

    Tolerance scales with magnitude (``rel_tol * max(|value|, 1)``) so small absolute
    values still get a sensible window. An exact 0 matches an allowed 0.
    """

    tol = rel_tol * max(abs(value), 1.0)
    return any(abs(value - a) <= tol for a in allowed)


def verify_grounding(
    narrative: str, allowed_numbers: list[float], *, rel_tol: float = DEFAULT_REL_TOL
) -> tuple[bool, list[float]]:
    """Verify every numeric token in ``narrative`` is in ``allowed_numbers``.

    Returns ``(ok, unmatched)``. ``ok`` is True when every extracted number matches an
    allowed number within ``rel_tol`` (a narrative with no numbers is trivially
    grounded). ``unmatched`` lists the offending numbers when not. Pure — this is the
    deterministic guard the whole grounding guarantee rests on.
    """

    unmatched = [
        n for n in extract_numbers(narrative) if not _matches_allowed(n, allowed_numbers, rel_tol)
    ]
    return (not unmatched, unmatched)


# ---------------------------------------------------------------------------
# Deterministic template narrative (the always-available fallback)
# ---------------------------------------------------------------------------
def render_template_narrative(bundle: EvidenceBundle) -> str:
    """Build a deterministic, fully-grounded narrative from the bundle's items.

    Composed entirely from the evidence items' own summaries (which were built from
    computed figures already in ``allowed_numbers``), so the result is grounded by
    construction. Ordering: metric window first, then baseline, then candidate causes,
    then dimensional contributions, then forecast. Pure / deterministic; needs no LLM.
    """

    by_kind: dict[str, list] = {}
    for item in bundle.items:
        by_kind.setdefault(item.kind, []).append(item)

    parts: list[str] = []
    for item in by_kind.get("metric_window", []):
        parts.append(item.summary)
    causes = by_kind.get("candidate_cause", [])
    if causes:
        if len(causes) == 1:
            parts.append("The most likely driver: " + causes[0].summary)
        else:
            parts.append(
                "The most likely drivers, in order: " + " ".join(c.summary for c in causes)
            )
    for item in by_kind.get("dimension_contribution", []):
        parts.append(item.summary)
    for item in by_kind.get("forecast", []):
        parts.append(item.summary)

    if not parts:
        # Nothing computed beyond the headline — neutral, grounded sentence.
        return "A monitored metric moved outside its expected range; see the evidence record for details."
    return " ".join(p.strip() for p in parts if p.strip())


# ---------------------------------------------------------------------------
# The narrator protocol + concrete narrators
# ---------------------------------------------------------------------------
class Narrator(Protocol):
    """Structural protocol for anything that narrates a finding from its bundle."""

    async def narrate(self, bundle: EvidenceBundle) -> NarrationResult: ...


class GroundedNarrator:
    """The production narrator: Claude over the bundle, guarded, with template fallback.

    Construct with an optional :class:`ClaudeNarrationClient`. When the client is
    ``None`` (no API key) every call goes straight to the deterministic template —
    so the narrator works identically with or without a key, only the *source* of the
    text differs.
    """

    def __init__(
        self,
        client: ClaudeNarrationClient | None = None,
        *,
        rel_tol: float = DEFAULT_REL_TOL,
    ) -> None:
        self._client = client
        self._rel_tol = float(rel_tol)

    async def narrate(self, bundle: EvidenceBundle) -> NarrationResult:
        """Narrate ``bundle``, returning grounded LLM text or the template fallback.

        Order of operations (the grounding guarantee):
        no client -> template; else call Claude -> if unusable (refusal / max_tokens /
        error / empty) -> template; else verify grounding -> if any unmatched number
        -> template; else accept the LLM narrative (``narrative_model`` = the model).
        """

        if self._client is None:
            return self._template(bundle, reason="no_api_key")

        outcome = await self._client.narrate(bundle)
        if not outcome.ok or not outcome.text:
            reason = outcome.refusal_category and f"refusal:{outcome.refusal_category}"
            reason = reason or outcome.error or outcome.stop_reason or "llm_unusable"
            return self._template(bundle, reason=reason, cache_read=outcome.cache_read_input_tokens)

        ok, unmatched = verify_grounding(
            outcome.text, bundle.allowed_numbers, rel_tol=self._rel_tol
        )
        if not ok:
            _log.warning(
                "narrative failed grounding guard; emitting template",
                extra={
                    "finding_id": str(bundle.finding_id),
                    "tenant_id": bundle.tenant_id,
                    "unmatched_numbers": unmatched,
                },
            )
            return self._template(
                bundle,
                reason="ungrounded_numbers",
                unmatched=tuple(unmatched),
                cache_read=outcome.cache_read_input_tokens,
            )

        return NarrationResult(
            narrative=outcome.text,
            narrative_model=outcome.model,
            source="llm",
            cache_read_input_tokens=outcome.cache_read_input_tokens,
        )

    def _template(
        self,
        bundle: EvidenceBundle,
        *,
        reason: str,
        unmatched: tuple[float, ...] = (),
        cache_read: int = 0,
    ) -> NarrationResult:
        return NarrationResult(
            narrative=render_template_narrative(bundle),
            narrative_model=None,
            source="template",
            reason=reason,
            unmatched_numbers=unmatched,
            cache_read_input_tokens=cache_read,
        )


class TemplateNarrator:
    """A narrator that always emits the deterministic template (never calls an LLM)."""

    async def narrate(self, bundle: EvidenceBundle) -> NarrationResult:
        return NarrationResult(
            narrative=render_template_narrative(bundle),
            narrative_model=None,
            source="template",
            reason="template_only",
        )


class FakeNarrator:
    """A test narrator returning canned LLM text, run through the real grounding guard.

    X4 uses this to exercise the whole analyze chain with no infra and no key. By
    default it echoes the deterministic template (which is grounded by construction),
    so a default :class:`FakeNarrator` yields a passing ``source="llm"`` result. Pass
    ``text=`` to test a specific narrative; pass an ungrounded ``text`` to assert the
    guard rejects it and falls back to the template.
    """

    def __init__(
        self,
        text: str | None = None,
        *,
        model: str = "fake-narrator",
        rel_tol: float = DEFAULT_REL_TOL,
    ) -> None:
        self._text = text
        self._model = model
        self._rel_tol = float(rel_tol)

    async def narrate(self, bundle: EvidenceBundle) -> NarrationResult:
        text = self._text if self._text is not None else render_template_narrative(bundle)
        ok, unmatched = verify_grounding(text, bundle.allowed_numbers, rel_tol=self._rel_tol)
        if not ok:
            return NarrationResult(
                narrative=render_template_narrative(bundle),
                narrative_model=None,
                source="template",
                reason="ungrounded_numbers",
                unmatched_numbers=tuple(unmatched),
            )
        return NarrationResult(narrative=text, narrative_model=self._model, source="llm")


def make_narrator(
    client: ClaudeNarrationClient | None, *, rel_tol: float = DEFAULT_REL_TOL
) -> GroundedNarrator:
    """Build the production :class:`GroundedNarrator` (template-only when ``client`` is None)."""

    return GroundedNarrator(client, rel_tol=rel_tol)
