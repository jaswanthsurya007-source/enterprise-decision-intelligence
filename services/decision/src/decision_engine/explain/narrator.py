"""OPTIONAL Opus-4.8 prose for a recommendation -- post-validated, discarded on mismatch.

THE NUMBERS RULE applies here exactly as in L3: the LLM writes only *prose*, never an
authoritative figure. This narrator:

* builds an ``AsyncAnthropic`` client **lazily and only if ``settings.anthropic_api_key``
  is set** (mirrors L3 ``make_narration_client``); with no key there is simply no prose
  and the recommendation keeps ``narrative=None``;
* calls ``claude-opus-4-8`` with adaptive thinking + ``output_config={"effort": "high"}``
  (NO temperature / budget_tokens) and ALWAYS checks ``stop_reason`` for refusal /
  ``max_tokens`` before trusting content;
* POST-VALIDATES the returned prose against ``impact.inputs`` (and the recommendation's
  own deterministic figures + the finding's computed numbers): every numeric token in the
  prose must match an allowed number within a small relative tolerance, else the prose is
  DISCARDED (``narrative=None``);
* NEVER raises into the decision flow -- any SDK/API/network error degrades to no prose.

So a recommendation's ``narrative`` is the LLM prose ONLY when it passed the guard;
otherwise it is ``None`` (the recommendation's grounded ``explanation_summary`` already
carries the human-readable, deterministic copy). The numeric extractor + grounding check
are reused from the L3 narrator so the guard is identical across layers.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from edis_contracts.decisions import Recommendation
from edis_contracts.findings import Finding
from edis_platform.logging import get_logger

if TYPE_CHECKING:
    from edis_platform.settings import Settings

_log = get_logger(__name__)

#: The prose model (verified): opus-4-8 with adaptive thinking + high effort.
NARRATION_MODEL = "claude-opus-4-8"
#: max_tokens for the short recommendation prose.
_MAX_TOKENS = 1024
#: Relative tolerance the post-validation guard allows when matching a prose number.
DEFAULT_REL_TOL = 0.02


# ---------------------------------------------------------------------------
# Numeric grounding guard (the deterministic check the WHOLE grounding
# guarantee rests on). This is intentionally identical in behaviour to the L3
# RCA narrator's guard so the guarantee is uniform across layers; it is ported
# in-line (not imported from the L3 service) because the decision engine depends
# only on the local libs, never on a sibling service's internals. Pure.
# ---------------------------------------------------------------------------
#: Magnitude suffixes that rescale a numeric token (percent does NOT rescale).
_SUFFIX_MULT = {"k": 1e3, "m": 1e6, "b": 1e9}

# (?<![A-Za-z0-9_]) keeps digits glued to an identifier (the "95" in
# "latency_p95") from matching. A trailing k/m/b only rescales when it is a
# standalone magnitude marker (?![A-Za-z]), never the 'm' in "min"/"ms". "%" is
# always a bare suffix.
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


def extract_numbers(text: str) -> list[float]:
    """Extract every numeric token from ``text`` as a list of floats.

    Handles ``$95,000``, ``-35.8%``, ``1.4k``, ``1,400``, ``9``. A ``%`` suffix does
    NOT rescale (``-35.8%`` -> ``-35.8``); ``k``/``m``/``b`` DO rescale (``1.4k`` ->
    ``1400``). Pure.
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
    grounded). ``unmatched`` lists the offending numbers when not. Pure.
    """

    unmatched = [
        n for n in extract_numbers(narrative) if not _matches_allowed(n, allowed_numbers, rel_tol)
    ]
    return (not unmatched, unmatched)


_SYSTEM_PROMPT = """\
You are the EDIS Decision narrator. You write a short, business-readable paragraph \
explaining a single recommended action to an operations or revenue analyst.

THE ONE ABSOLUTE RULE - GROUNDING:
- You are given a recommendation and the EXACT computed inputs behind its impact \
estimate (the ALLOWED NUMBERS). Those numbers were produced by deterministic code, not \
by you.
- You MUST NOT invent, infer, estimate, annualize, sum, average, or otherwise compute \
any number. Every numeric token you write - every amount, percentage, count, day count, \
confidence - MUST correspond to a value in the ALLOWED NUMBERS list.
- If a figure you want to state is not in the list, state it qualitatively instead \
("a large recovery", "high confidence") rather than guessing.

STYLE:
- 2 to 3 sentences, plain business English, no markdown, no preamble.
- Lead with the action, then the estimated impact, then the confidence. Do not restate \
the root-cause detail at length - a separate finding owns that.\
"""


def allowed_numbers_for(finding: Finding, rec: Recommendation) -> list[float]:
    """The whitelist of numbers the prose may cite (the deterministic facts only).

    Drawn from the recommendation's :class:`ImpactEstimate` (value / band / horizon /
    every ``inputs`` figure), its confidence (value + components), priority, and the
    source finding's computed figures. The prose is post-validated against THIS set, so
    the LLM cannot smuggle in a fabricated number. Pure.
    """

    allowed: list[float] = [
        rec.impact.value,
        rec.impact.value_low,
        rec.impact.value_high,
        float(rec.impact.horizon_days),
        rec.confidence.value,
        rec.priority_score,
        float(rec.priority_rank),
        finding.observed_value,
        finding.expected_value,
        finding.deviation,
        finding.deviation_pct,
        finding.severity,
        finding.confidence,
        finding.score,
    ]
    allowed.extend(float(v) for v in rec.impact.inputs.values())
    allowed.extend(float(v) for v in rec.confidence.components.values())
    for cause in finding.candidate_causes:
        allowed.append(cause.correlation)
        allowed.append(float(cause.lag_minutes))
        if cause.contribution_pct is not None:
            allowed.append(cause.contribution_pct)
        allowed.append(cause.observed_delta)
    return allowed


def _render_user_turn(finding: Finding, rec: Recommendation, allowed: list[float]) -> str:
    """Render the deterministic user turn: the action + the allowed-numbers whitelist."""

    def _fmt(v: float) -> str:
        return str(int(v)) if v == int(v) else f"{v:.6f}".rstrip("0").rstrip(".")

    return (
        "RECOMMENDED ACTION:\n"
        f"  title: {rec.title}\n"
        f"  action_type: {rec.action_type}\n"
        f"  impact: {rec.impact.direction} ~{_fmt(rec.impact.value)} {rec.impact.unit} "
        f"over {rec.impact.horizon_days} day(s) (method={rec.impact.method})\n"
        f"  confidence: {_fmt(rec.confidence.value)}\n"
        f"  source metric: {finding.metric_key} "
        f"(deviation {_fmt(finding.deviation_pct)}%)\n\n"
        "ALLOWED NUMBERS (you may ONLY write numbers from this list):\n"
        f"{', '.join(_fmt(n) for n in allowed)}\n\n"
        "Write the grounded explanation now, citing only numbers from the list."
    )


class RecommendationNarrator:
    """Optional Opus prose for a recommendation, with a hard post-validation guard.

    Construct with an optional pre-built ``AsyncAnthropic`` client; when it is ``None``
    (no key) :meth:`narrate` returns ``None`` immediately (no prose). Use
    :func:`make_recommendation_narrator` to apply the lazy + key-guarded rule.
    """

    def __init__(
        self, client=None, *, model: str = NARRATION_MODEL, rel_tol: float = DEFAULT_REL_TOL
    ) -> None:
        self._client = client
        self._model = model
        self._rel_tol = float(rel_tol)

    async def narrate(self, finding: Finding, rec: Recommendation) -> str | None:
        """Return grounded prose, or ``None`` (no key / unusable / failed the guard).

        Never raises: any SDK/API/network/refusal/empty result -> ``None``. The returned
        prose, when not ``None``, has passed the numeric grounding guard against
        :func:`allowed_numbers_for`.
        """

        if self._client is None:
            return None

        allowed = allowed_numbers_for(finding, rec)
        user_turn = _render_user_turn(finding, rec, allowed)
        try:
            async with self._client.messages.stream(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": "high"},
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_turn}],
            ) as stream:
                msg = await stream.get_final_message()
        except Exception as exc:  # noqa: BLE001 - never propagate into the decision flow
            _log.warning(
                "recommendation prose failed; leaving narrative=None",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            return None

        # Always check stop_reason before trusting content.
        stop_reason = getattr(msg, "stop_reason", None)
        if stop_reason in ("refusal", "max_tokens"):
            _log.info(
                "recommendation prose unusable; leaving narrative=None",
                extra={"stop_reason": stop_reason},
            )
            return None

        text = self._extract_text(msg)
        if not text:
            return None

        # POST-VALIDATE against impact.inputs (+ the deterministic figures).
        ok, unmatched = verify_grounding(text, allowed, rel_tol=self._rel_tol)
        if not ok:
            _log.warning(
                "recommendation prose failed grounding guard; discarding (narrative=None)",
                extra={
                    "tenant_id": rec.tenant_id,
                    "recommendation_id": str(rec.recommendation_id),
                    "unmatched_numbers": unmatched,
                },
            )
            return None
        return text

    @staticmethod
    def _extract_text(msg) -> str:
        """Concatenate text from ``msg.content`` blocks where ``block.type == 'text'``."""

        parts: list[str] = []
        for block in getattr(msg, "content", []) or []:
            if getattr(block, "type", None) == "text":
                parts.append(getattr(block, "text", "") or "")
        return "".join(parts).strip()

    async def aclose(self) -> None:
        """Close the underlying SDK HTTP client (best-effort)."""

        if self._client is None:
            return
        close = getattr(self._client, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:  # noqa: BLE001
                pass


def make_recommendation_narrator(
    settings: "Settings", *, rel_tol: float = DEFAULT_REL_TOL
) -> RecommendationNarrator:
    """Build the narrator, constructing the Opus client lazily iff a key is set.

    Mirrors L3's ``make_narration_client``: with no ``anthropic_api_key`` (or no SDK) the
    narrator holds no client and always returns ``None`` prose -- the engine works fully
    with no key. This is the single place the "lazy, only-with-key" rule is enforced for
    recommendation prose.
    """

    api_key = getattr(settings, "anthropic_api_key", None)
    if not api_key:
        return RecommendationNarrator(None, rel_tol=rel_tol)
    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=api_key)
    except Exception as exc:  # noqa: BLE001 - SDK missing / construction failure
        _log.warning(
            "could not build Claude client; recommendation prose disabled",
            extra={"error": str(exc)},
        )
        return RecommendationNarrator(None, rel_tol=rel_tol)
    return RecommendationNarrator(client, rel_tol=rel_tol)
