"""Lazy, key-guarded ``AsyncAnthropic`` factory — returns ``None`` without a key.

This MIRRORS the L3 ``make_narration_client`` discipline: the SDK client is built
**lazily and only when ``settings.anthropic_api_key`` is set**. With no key,
:func:`make_anthropic_client` returns ``None`` — and the (P2) agent loop then takes the
deterministic rule-driven path that still calls the real read-only tools and templates
a grounded, cited answer from real retrieved data. The whole copilot therefore runs
OFFLINE with no ANTHROPIC key.

``anthropic`` is imported lazily inside the factory, so importing this module (and the
service) needs neither the SDK at config time nor any key. Model ids + call shape live
in :mod:`edis_copilot.llm.models`; the cached system prompt in :mod:`edis_copilot.llm.prompts`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from edis_platform.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from edis_platform.settings import Settings

_log = get_logger(__name__)


def make_anthropic_client(settings: "Settings") -> Any | None:
    """Build an ``anthropic.AsyncAnthropic`` iff ``settings.anthropic_api_key`` is set.

    Returns ``None`` when no key is configured (offline mode) or when the SDK cannot be
    constructed — the copilot then uses its deterministic rule-driven agent. This is the
    single place the "lazy, only-with-key" rule is enforced for L5, mirroring the L3
    narration client.
    """

    api_key = getattr(settings, "anthropic_api_key", None)
    if not api_key:
        _log.info("no ANTHROPIC_API_KEY; copilot will use the offline rule-driven agent")
        return None
    try:
        from anthropic import AsyncAnthropic  # lazy: SDK not needed to import this module

        return AsyncAnthropic(api_key=api_key)
    except Exception as exc:  # noqa: BLE001 - SDK missing / construction failure
        _log.warning(
            "could not build AsyncAnthropic; copilot will use the offline agent",
            extra={"error": str(exc), "error_type": type(exc).__name__},
        )
        return None


def has_anthropic_key(settings: "Settings") -> bool:
    """True iff an Anthropic key is configured (cheap check, builds nothing)."""

    return bool(getattr(settings, "anthropic_api_key", None))
