"""Async Claude narration client — built lazily, never without an API key.

Wraps ``anthropic.AsyncAnthropic`` to narrate one finding from its EvidenceBundle,
following the verified Claude-API rules for ``claude-opus-4-8``:

* The SDK client is constructed **lazily and only if ``settings.anthropic_api_key``
  is set**. If it is absent the client is never built — :func:`make_narration_client`
  returns ``None`` and the narrator goes straight to the deterministic template path.
* Narration uses **async streaming + adaptive thinking + high effort**::

      async with client.messages.stream(
          model="claude-opus-4-8",
          max_tokens=2000,
          thinking={"type": "adaptive", "display": "summarized"},
          output_config={"effort": "high"},
          system=SYSTEM_BLOCKS,
          messages=[...],
      ) as stream:
          msg = await stream.get_final_message()

  No ``temperature`` / ``top_p`` / ``top_k`` / ``budget_tokens`` (all 400 on
  opus-4-8). Text is read only from ``msg.content`` blocks where ``block.type ==
  "text"``.
* ``msg.stop_reason`` is ALWAYS checked before trusting content. ``"refusal"`` (with
  ``stop_details.category``) and ``"max_tokens"`` both yield an UNUSABLE outcome so
  the narrator discards and falls back to the template.
* Prompt caching: the stable system prompt is sent first as cached system blocks
  (see :mod:`prompts`); ``usage.cache_read_input_tokens`` is surfaced on the outcome
  as a metric. (The min cacheable prefix on opus-4-8 is 4096 tokens, so a cache hit
  is only expected once the system prefix exceeds that — correctness never depends on
  it.)
* Any API error / network failure is caught and returned as an UNUSABLE outcome
  (``ok=False``) — the narrator never raises into detection.

This module imports ``anthropic`` lazily inside the constructor, so importing it
(and the whole service) needs neither the SDK at runtime-config time nor any key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from edis_contracts.findings import EvidenceBundle
from edis_platform.logging import get_logger

from edis_intelligence.grounding.prompts import render_evidence_user_turn, system_blocks

if TYPE_CHECKING:
    from edis_platform.settings import Settings

_log = get_logger(__name__)

#: The narration model (verified): opus-4-8, 1M ctx, 128K max output.
NARRATION_MODEL = "claude-opus-4-8"
#: max_tokens for a short grounded narrative (the build spec value).
_MAX_TOKENS = 2000


@dataclass(frozen=True)
class NarrationOutcome:
    """The result of one narration attempt.

    ``ok`` is True only when the model produced usable text with a clean stop reason
    (``end_turn`` / ``stop_sequence``). On a refusal, ``max_tokens`` truncation, an
    API error, or an empty body, ``ok`` is False and the narrator discards the text
    and emits the deterministic template (``narrative_model=None``).
    """

    ok: bool
    text: str | None
    model: str | None
    stop_reason: str | None = None
    refusal_category: str | None = None
    error: str | None = None
    #: Prompt-cache metric (tokens served from cache); 0 when no hit / unavailable.
    cache_read_input_tokens: int = 0
    usage: dict[str, int] = field(default_factory=dict)


class ClaudeNarrationClient:
    """Lazy async wrapper over ``anthropic.AsyncAnthropic`` for grounded narration."""

    def __init__(self, api_key: str, *, model: str = NARRATION_MODEL) -> None:
        """Construct the SDK client. Only called when an API key is present.

        ``anthropic`` is imported here (not at module import) so the service imports
        cleanly even if the SDK is not installed in a given environment.
        """

        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def narrate(self, bundle: EvidenceBundle) -> NarrationOutcome:
        """Narrate one finding from ``bundle`` (the only context the model sees).

        Returns a :class:`NarrationOutcome`. Never raises: any SDK/API/network error
        is captured into ``ok=False`` so the caller can fall back to the template.
        """

        user_turn = render_evidence_user_turn(bundle)
        messages = [{"role": "user", "content": user_turn}]

        try:
            async with self._client.messages.stream(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": "high"},
                system=system_blocks(),
                messages=messages,
            ) as stream:
                msg = await stream.get_final_message()
        except Exception as exc:  # noqa: BLE001 - never propagate into detection
            _log.warning(
                "claude narration failed; falling back to template",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            return NarrationOutcome(
                ok=False, text=None, model=None, error=f"{type(exc).__name__}: {exc}"
            )

        return self._interpret(msg)

    def _interpret(self, msg) -> NarrationOutcome:
        """Map an SDK message to a :class:`NarrationOutcome`, enforcing stop-reason rules."""

        stop_reason = getattr(msg, "stop_reason", None)
        usage = getattr(msg, "usage", None)
        cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0) if usage else 0
        usage_dict: dict[str, int] = {}
        if usage is not None:
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            ):
                v = getattr(usage, k, None)
                if v is not None:
                    usage_dict[k] = int(v)

        # Refusal -> discard, fall back to template.
        if stop_reason == "refusal":
            details = getattr(msg, "stop_details", None)
            category = getattr(details, "category", None) if details is not None else None
            _log.warning(
                "claude refused narration; falling back to template",
                extra={"refusal_category": category},
            )
            return NarrationOutcome(
                ok=False,
                text=None,
                model=None,
                stop_reason=stop_reason,
                refusal_category=category,
                cache_read_input_tokens=cache_read,
                usage=usage_dict,
            )

        # Truncated -> discard (an incomplete narrative is not trustworthy).
        if stop_reason == "max_tokens":
            _log.warning("claude narration hit max_tokens; falling back to template")
            return NarrationOutcome(
                ok=False,
                text=None,
                model=None,
                stop_reason=stop_reason,
                cache_read_input_tokens=cache_read,
                usage=usage_dict,
            )

        # Read text only from text blocks.
        text = self._extract_text(msg)
        if not text:
            return NarrationOutcome(
                ok=False,
                text=None,
                model=None,
                stop_reason=stop_reason,
                error="empty narrative",
                cache_read_input_tokens=cache_read,
                usage=usage_dict,
            )

        return NarrationOutcome(
            ok=True,
            text=text,
            model=self._model,
            stop_reason=stop_reason,
            cache_read_input_tokens=cache_read,
            usage=usage_dict,
        )

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

        close = getattr(self._client, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:  # noqa: BLE001
                pass


def make_narration_client(settings: "Settings") -> ClaudeNarrationClient | None:
    """Build a :class:`ClaudeNarrationClient` iff ``settings.anthropic_api_key`` is set.

    Returns ``None`` when no key is configured — the narrator then never attempts an
    API call and uses the deterministic template path. This is the single place the
    "lazy, only-with-key" rule is enforced.
    """

    api_key = getattr(settings, "anthropic_api_key", None)
    if not api_key:
        return None
    try:
        return ClaudeNarrationClient(api_key)
    except Exception as exc:  # noqa: BLE001 - SDK missing / construction failure
        _log.warning(
            "could not build Claude client; narration will use template path",
            extra={"error": str(exc)},
        )
        return None
