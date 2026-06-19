"""OpenTelemetry span helper for the manual copilot agent loop (no-op without OTel).

The architecture calls for per-iteration OTel spans on the manual loop (§5.5). This
module wraps that in one tiny helper, :func:`span`, an async context manager that:

* opens a span named ``copilot.<name>`` carrying ``tenant_id`` / ``trace_id`` and any
  extra attributes (run through :mod:`edis_copilot.telemetry.redact` so nothing secret-shaped is
  recorded), and
* degrades to a **no-op** when ``opentelemetry`` is not importable — so the loop runs
  identically in CI with no collector and no extra dependency wired.

Importing this module never imports OTel eagerly; it is resolved lazily inside
:func:`span`. Pure/lazy — safe to import with no infra.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from edis_copilot.telemetry.redact import redact_tool_input

_TRACER_NAME = "edis.copilot"


@contextlib.asynccontextmanager
async def span(
    name: str,
    *,
    tenant_id: str | None = None,
    trace_id: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> AsyncIterator[None]:
    """Open a ``copilot.<name>`` OTel span (no-op without OpenTelemetry).

    Attributes are scrubbed via :func:`redact_tool_input` so a tool input / query
    preview never carries a secret into a trace. Any failure constructing the span is
    swallowed — telemetry must never break the request path.
    """

    try:
        from opentelemetry import trace  # lazy; OTel stays optional in CI
    except Exception:  # pragma: no cover - OTel usually present, defensive
        yield
        return

    tracer = trace.get_tracer(_TRACER_NAME)
    attrs: dict[str, Any] = {}
    if tenant_id is not None:
        attrs["edis.tenant_id"] = tenant_id
    if trace_id is not None:
        attrs["edis.trace_id"] = trace_id
    for k, v in (attributes or {}).items():
        scrubbed = redact_tool_input(v)
        # OTel attributes must be primitives; stringify anything structured.
        attrs[k] = scrubbed if isinstance(scrubbed, (str, int, float, bool)) else str(scrubbed)

    with tracer.start_as_current_span(f"copilot.{name}", attributes=attrs):
        yield
