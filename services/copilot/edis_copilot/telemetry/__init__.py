"""Copilot telemetry: per-iteration OTel spans + log/trace redaction.

* :mod:`edis_copilot.telemetry.tracing` — :func:`span`, an async context manager that opens a
  ``copilot.<name>`` OTel span (no-op without OpenTelemetry) for the manual agent loop.
* :mod:`edis_copilot.telemetry.redact` — :func:`redact_text` / :func:`redact_tool_input`, pure
  helpers that scrub secret-shaped content and clip previews before anything is logged
  or recorded as a span attribute.

Both are lazy/pure and safe to import with no infra and no key.
"""

from __future__ import annotations

from edis_copilot.telemetry.redact import redact_text, redact_tool_input
from edis_copilot.telemetry.tracing import span

__all__ = ["redact_text", "redact_tool_input", "span"]
