"""Structured JSON logging with trace correlation and secret redaction.

Every log line is a single JSON object. When an OpenTelemetry span is active the
formatter injects ``trace_id`` / ``span_id`` so logs correlate with traces across
all seven layers. A redaction pass scrubs anything that looks like a secret
(keys, tokens, passwords, JWTs) from both the message and structured ``extra``
fields. Importing this module has no side effects; you must call
:func:`configure_logging` explicitly (typically at service startup).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

# Fields the LogRecord ships with that we render explicitly or never emit raw.
_RESERVED_RECORD_FIELDS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)

# Substring markers (case-insensitive) that mark a structured field as sensitive.
_SENSITIVE_KEY_MARKERS = (
    "secret",
    "password",
    "passwd",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "jwt",
    "credential",
    "cookie",
    "session",
    "bearer",
)

_REDACTED = "***REDACTED***"

# Heuristic patterns for secrets embedded in free-text messages.
_MESSAGE_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Bearer <token>
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"), r"\1" + _REDACTED),
    # JWT-shaped triple-segment base64url tokens
    (re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), _REDACTED),
    # key=value / key: value where the key looks sensitive
    (
        re.compile(
            r"(?i)\b([A-Za-z0-9_\-]*(?:secret|password|passwd|token|api[_\-]?key|"
            r"credential|bearer)[A-Za-z0-9_\-]*)\s*[=:]\s*([^\s,;]+)"
        ),
        r"\1=" + _REDACTED,
    ),
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def _redact_message(text: str) -> str:
    for pattern, repl in _MESSAGE_REDACTIONS:
        text = pattern.sub(repl, text)
    return text


def _redact_value(key: str, value: Any) -> Any:
    if _is_sensitive_key(key):
        return _REDACTED
    if isinstance(value, str):
        return _redact_message(value)
    if isinstance(value, dict):
        return {k: _redact_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(key, v) for v in value]
    return value


def _current_trace_context() -> tuple[str | None, str | None]:
    """Best-effort current trace/span ids; returns (None, None) without OTel."""

    try:
        from opentelemetry import trace  # local import: OTel stays optional
    except Exception:  # pragma: no cover - otel always installed but defensive
        return None, None

    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if not ctx or not getattr(ctx, "is_valid", False):
        return None, None
    return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"


class JsonFormatter(logging.Formatter):
    """Renders a :class:`logging.LogRecord` as one redacted JSON object."""

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        message = _redact_message(record.getMessage())

        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": self.service_name,
            "message": message,
        }

        trace_id, span_id = _current_trace_context()
        if trace_id:
            payload["trace_id"] = trace_id
        if span_id:
            payload["span_id"] = span_id

        # Merge user-supplied structured fields (logger.info(..., extra={...})).
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_FIELDS or key.startswith("_"):
                continue
            if key in payload:
                continue
            payload[key] = _redact_value(key, value)

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(service_name: str, level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger (idempotent)."""

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service_name=service_name))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    """Return a named logger (configuration is global via the root logger)."""

    return logging.getLogger(name)
