"""Redaction helpers for copilot telemetry — never log a secret or a raw prompt token.

The agent loop logs tool calls, tool inputs, and answer metadata. Two risks: (1) a user
question or retrieved document could contain a secret (a pasted token, an API key); (2)
logging the full answer/prompt bloats logs and may leak tenant content. These helpers
keep what we log small and scrubbed, layered ON TOP of the platform JSON formatter's own
key/message redaction (so this is defense in depth, not a replacement).

Pure functions, no I/O — used by :mod:`edis_copilot.telemetry.tracing` and the agent loop when
building span attributes / log ``extra`` dicts.
"""

from __future__ import annotations

import re
from typing import Any

_REDACTED = "***REDACTED***"

# JWT-shaped tokens, Bearer headers, and obvious key=value secrets in free text.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"), r"\1" + _REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), _REDACTED),
    (re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,})"), _REDACTED),  # anthropic/voyage-style keys
)

_SENSITIVE_KEYS = ("token", "secret", "password", "api_key", "apikey", "authorization", "bearer")


def redact_text(text: str, *, max_len: int = 240) -> str:
    """Scrub secret-shaped substrings from ``text`` and clip it to ``max_len`` chars.

    Used to log a *preview* of a question / answer — never the full body — with any
    token-shaped content removed. Pure.
    """

    if not isinstance(text, str):
        text = str(text)
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


def redact_tool_input(tool_input: Any) -> Any:
    """Recursively redact a tool-input object for safe logging.

    Drops values under obviously sensitive keys and scrubs string values. The model's
    tool args are normally benign (a metric key, a query) but a query string can contain
    pasted secrets, so we scrub before it reaches a log. Pure.
    """

    if isinstance(tool_input, dict):
        out: dict[str, Any] = {}
        for k, v in tool_input.items():
            if any(marker in str(k).lower() for marker in _SENSITIVE_KEYS):
                out[k] = _REDACTED
            else:
                out[k] = redact_tool_input(v)
        return out
    if isinstance(tool_input, (list, tuple)):
        return [redact_tool_input(v) for v in tool_input]
    if isinstance(tool_input, str):
        return redact_text(tool_input, max_len=200)
    return tool_input
