"""Intent classification: optional Haiku 4.5 structured output + a DETERMINISTIC fallback.

Maps a :class:`~edis_contracts.findings.Finding` to a :class:`PlaybookIntent`. Two
paths, and the engine works fully on the second alone:

1. **LLM (optional).** :class:`LlmIntentClassifier` calls ``claude-haiku-4-5`` with
   the SDK's structured-output helper -- ``await client.messages.parse(
   model="claude-haiku-4-5", max_tokens=512, messages=[...],
   output_format=_IntentChoice)`` -> ``response.parsed_output`` (a constrained enum
   model). **Haiku does NOT accept the ``effort`` parameter**, so it is never sent.
   The ``AsyncAnthropic`` client is built LAZILY and ONLY if a key is set; any
   error / refusal / unusable result silently degrades to the rule-based path. The
   classifier NEVER raises into the decision flow and NEVER produces a number.

2. **Rule-based (always available).** :class:`RuleBasedIntentClassifier` maps
   ``FindingKind`` + ``metric_key`` (+ dimensions) to an intent with a small, ordered
   rule table. This is the fallback used when there is no key / the SDK is
   unavailable / the LLM returns garbage -- and it is what runs in CI and in the
   no-key demo. It is pure and deterministic.

:class:`IntentClassifier` is the production composite: try the LLM (if configured),
validate the result is a real :class:`PlaybookIntent`, and on ANY problem fall back
to the deterministic rule. Mirrors the L3 narrator's lazy + key-guarded +
degrade-to-deterministic pattern exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from edis_contracts.findings import Finding, FindingKind
from edis_platform.logging import get_logger
from pydantic import BaseModel

from decision_engine.synthesis.playbooks.base import PlaybookIntent

if TYPE_CHECKING:
    from edis_platform.settings import Settings

_log = get_logger(__name__)

#: The classification model (verified): haiku-4-5, structured outputs, no effort.
CLASSIFIER_MODEL = "claude-haiku-4-5"
#: max_tokens for the tiny structured classification call.
_MAX_TOKENS = 512


# ---------------------------------------------------------------------------
# Deterministic rule-based classifier (always available; pure)
# ---------------------------------------------------------------------------
#: Metric keys that signal an operational/reliability problem (ops domain).
_OPS_METRICS = frozenset({"error_rate", "latency_p95", "latency", "error_count"})
#: Revenue/order metrics whose anomalies map to operational mitigation when an ops
#: cause leads them (the demo), else investigation.
_REVENUE_METRICS = frozenset({"revenue", "orders", "gmv"})


def _has_ops_cause(finding: Finding) -> bool:
    """True if a candidate cause is an ops metric on a named service (RCA leader)."""

    for cause in finding.candidate_causes:
        if cause.metric_key in _OPS_METRICS and cause.dimensions.get("service"):
            return True
    return False


def classify_by_rule(finding: Finding) -> PlaybookIntent:
    """Deterministically map a finding to a :class:`PlaybookIntent` (pure).

    Ordered rules (first match wins):

    * an ops metric (error_rate / latency_p95 ...) anomaly -> ``operational_fix``
      (mitigate the failing service);
    * a revenue/orders anomaly WITH a leading ops cause -> ``operational_fix``
      (the demo: EMEA revenue drop driven by checkout-api latency);
    * a revenue/orders anomaly with NO ops cause -> ``investigate``;
    * a ROOT_CAUSE finding -> ``operational_fix`` if it has an ops cause, else
      ``investigate``;
    * anything else -> ``investigate`` (the safe, always-valid default).
    """

    metric = finding.metric_key
    if metric in _OPS_METRICS:
        return PlaybookIntent.OPERATIONAL_FIX
    if metric in _REVENUE_METRICS:
        return (
            PlaybookIntent.OPERATIONAL_FIX
            if _has_ops_cause(finding)
            else PlaybookIntent.INVESTIGATE
        )
    if finding.kind == FindingKind.ROOT_CAUSE:
        return (
            PlaybookIntent.OPERATIONAL_FIX
            if _has_ops_cause(finding)
            else PlaybookIntent.INVESTIGATE
        )
    return PlaybookIntent.INVESTIGATE


class RuleBasedIntentClassifier:
    """A classifier that always uses the deterministic rule (never calls an LLM)."""

    async def classify(self, finding: Finding) -> PlaybookIntent:
        return classify_by_rule(finding)


# ---------------------------------------------------------------------------
# Optional LLM classifier (Haiku 4.5 structured output; lazy + key-guarded)
# ---------------------------------------------------------------------------
class _IntentChoice(BaseModel):
    """The constrained structured-output schema Haiku fills in.

    A single field whose value space is the :class:`PlaybookIntent` enum, so the
    model can only return one of the seven valid playbook intents -- the structured
    output is self-validating.
    """

    intent: PlaybookIntent


def _render_classification_prompt(finding: Finding) -> str:
    """Build the tiny user turn describing the finding (computed facts only).

    The classifier sees only structural facts (kind, metric, dimensions, the leading
    candidate causes). It never sees -- and cannot influence -- any impact number;
    its sole job is to pick a playbook intent.
    """

    causes = []
    for c in finding.candidate_causes[:3]:
        svc = c.dimensions.get("service", "")
        causes.append(
            f"- {c.metric_key} (service={svc or 'n/a'}, corr={c.correlation:+.2f}, "
            f"dir={c.direction})"
        )
    causes_block = "\n".join(causes) if causes else "- (none)"
    dims = ", ".join(f"{k}={v}" for k, v in sorted(finding.dimensions.items())) or "(none)"
    return (
        "Classify this monitoring finding into exactly one playbook intent.\n\n"
        f"kind: {finding.kind.value}\n"
        f"metric_key: {finding.metric_key}\n"
        f"dimensions: {dims}\n"
        f"deviation_pct: {finding.deviation_pct:.1f}\n"
        "leading candidate causes:\n"
        f"{causes_block}\n\n"
        "Pick the single best playbook intent for acting on this finding."
    )


_SYSTEM = (
    "You are EDIS's decision router. Given a computed monitoring finding, choose the "
    "single most appropriate playbook intent. You never invent numbers; you only pick "
    "an intent label. If an availability/latency/error regression on a named service "
    "is driving the anomaly, prefer operational_fix."
)


class LlmIntentClassifier:
    """Lazy async wrapper over ``anthropic.AsyncAnthropic`` for Haiku classification.

    Built only when an API key is present (see :func:`make_intent_classifier`).
    ``anthropic`` is imported in the constructor (not at module import) so the
    service imports cleanly even without the SDK installed.
    """

    def __init__(self, api_key: str, *, model: str = CLASSIFIER_MODEL) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def classify(self, finding: Finding) -> PlaybookIntent | None:
        """Return the classified intent, or ``None`` if the call is unusable.

        Uses ``messages.parse`` with ``output_format=_IntentChoice`` and reads
        ``parsed_output``. Haiku does NOT take ``effort``, so it is never passed.
        Never raises: any SDK / API / refusal / parse failure returns ``None`` so the
        composite classifier falls back to the deterministic rule.
        """

        messages = [{"role": "user", "content": _render_classification_prompt(finding)}]
        try:
            response = await self._client.messages.parse(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM,
                messages=messages,
                output_format=_IntentChoice,
            )
        except Exception as exc:  # noqa: BLE001 - never propagate into the decision flow
            _log.warning(
                "haiku intent classification failed; using rule-based fallback",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            return None

        # Refusal / unusable stop reason -> discard, fall back.
        if getattr(response, "stop_reason", None) == "refusal":
            _log.warning("haiku refused intent classification; using rule-based fallback")
            return None

        parsed = getattr(response, "parsed_output", None)
        if parsed is None or not isinstance(getattr(parsed, "intent", None), PlaybookIntent):
            return None
        return parsed.intent

    async def aclose(self) -> None:
        """Close the underlying SDK HTTP client (best-effort)."""

        close = getattr(self._client, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Composite classifier (LLM if configured, else rule) + the Protocol
# ---------------------------------------------------------------------------
class Classifier(Protocol):
    """Structural protocol for anything that classifies a finding's intent."""

    async def classify(self, finding: Finding) -> PlaybookIntent: ...


@dataclass(frozen=True)
class ClassificationResult:
    """A classification plus its provenance (which path produced it)."""

    intent: PlaybookIntent
    source: str  # "llm" | "rule"


class IntentClassifier:
    """Production classifier: try the LLM (if present), else the deterministic rule.

    Construct with an optional :class:`LlmIntentClassifier`. When it is ``None`` (no
    key) every call goes straight to the rule -- so the classifier works identically
    with or without a key, only the *source* differs. The intent is ALWAYS a valid
    :class:`PlaybookIntent`, and no number ever flows through here.
    """

    def __init__(self, llm: LlmIntentClassifier | None = None) -> None:
        self._llm = llm

    async def classify(self, finding: Finding) -> PlaybookIntent:
        """Return a valid :class:`PlaybookIntent` (LLM result or rule fallback)."""

        return (await self.classify_detailed(finding)).intent

    async def classify_detailed(self, finding: Finding) -> ClassificationResult:
        """Like :meth:`classify` but also reports the source ("llm" / "rule")."""

        if self._llm is not None:
            intent = await self._llm.classify(finding)
            if isinstance(intent, PlaybookIntent):
                return ClassificationResult(intent=intent, source="llm")
        return ClassificationResult(intent=classify_by_rule(finding), source="rule")

    async def aclose(self) -> None:
        if self._llm is not None:
            await self._llm.aclose()


def make_intent_classifier(settings: "Settings", *, use_llm: bool = True) -> IntentClassifier:
    """Build the production :class:`IntentClassifier`.

    Builds the lazy Haiku client iff ``use_llm`` and ``settings.anthropic_api_key`` is
    set; otherwise the composite runs rule-only. This is the single place the "lazy,
    only-with-key" rule is enforced for classification (mirrors L3's
    ``make_narration_client``).
    """

    api_key = getattr(settings, "anthropic_api_key", None)
    if not use_llm or not api_key:
        return IntentClassifier(None)
    try:
        return IntentClassifier(LlmIntentClassifier(api_key))
    except Exception as exc:  # noqa: BLE001 - SDK missing / construction failure
        _log.warning(
            "could not build Haiku classifier; classification will use the rule path",
            extra={"error": str(exc)},
        )
        return IntentClassifier(None)
