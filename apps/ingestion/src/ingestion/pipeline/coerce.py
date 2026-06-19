"""Normalize messy *source* types before per-domain validation.

This is the first thing that touches an untrusted record. It corrects the well-
known source quirks the architecture calls out at the edge — currency-as-string,
epoch-ms-vs-ISO-vs-``mm/dd/yyyy`` timestamps, numeric strings, blank-as-null — so
the strict ``extra="forbid"`` per-domain models (``SalesPayloadV1`` /
``OpsPayloadV1``) can validate a clean shape and *only* reject genuine schema
drift, not formatting noise.

Design rules:

* Coercion is **total and lossless about intent**: it never invents a value. If a
  field is missing it is left missing (the per-domain model decides if that is an
  error); if a value is uncoercible the raw value is left in place so validation
  produces a precise, auditable error.
* Coercion is **pure** — no IO, deterministic — so it is trivially unit-testable.
* All datetimes come out **tz-aware UTC**.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

# --- timestamps ---------------------------------------------------------------

# Threshold separating epoch-seconds from epoch-milliseconds: ~ year 2001 in ms,
# but a value this large can never be a plausible epoch-seconds business event
# (it would be year 33658), so any int/float at or above it is treated as ms.
_EPOCH_MS_THRESHOLD = 1e11

_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y",
    "%d/%m/%Y",  # tried last; ambiguous dd/mm vs mm/dd resolved by mm/dd first
)

_CURRENCY_STRIP = re.compile(r"[,$€£¥\s]")


def _as_utc(dt: datetime) -> datetime:
    """Return ``dt`` as tz-aware UTC (naive inputs are *assumed* UTC)."""

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def coerce_timestamp(value: Any) -> Any:
    """Coerce a messy timestamp to a tz-aware UTC :class:`datetime`.

    Accepts: an existing ``datetime`` (normalized to UTC), epoch seconds or
    milliseconds (int/float, or numeric string), ISO-8601, ``mm/dd/yyyy`` and a
    few other common shapes. Returns the *original* value untouched when it cannot
    be parsed, so the downstream validator raises a precise error rather than this
    layer guessing.
    """

    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)

    # Numeric epoch (int/float or a string that is purely numeric).
    epoch: float | None = None
    if isinstance(value, bool):  # bool is an int subclass — never an epoch
        return value
    if isinstance(value, (int, float)):
        epoch = float(value)
    elif isinstance(value, str):
        s = value.strip()
        if s and re.fullmatch(r"-?\d+(\.\d+)?", s):
            epoch = float(s)
    if epoch is not None:
        seconds = epoch / 1000.0 if abs(epoch) >= _EPOCH_MS_THRESHOLD else epoch
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return value

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Python 3.11+ fromisoformat handles most ISO variants incl. "Z".
        try:
            return _as_utc(datetime.fromisoformat(s.replace("Z", "+00:00")))
        except ValueError:
            pass
        for fmt in _DATE_FORMATS:
            try:
                return _as_utc(datetime.strptime(s, fmt))
            except ValueError:
                continue
    return value


def coerce_float(value: Any) -> Any:
    """Coerce currency-as-string / numeric strings to ``float``.

    Strips currency symbols, thousands separators and whitespace. Leaves the raw
    value in place when it is not numeric (validator surfaces the error).
    """

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = _CURRENCY_STRIP.sub("", value.strip())
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return value
    return value


def coerce_int(value: Any) -> Any:
    """Coerce numeric strings / whole floats to ``int``; pass through otherwise."""

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            try:
                f = float(s)
                return int(f) if f.is_integer() else f
            except ValueError:
                return value
    return value


def _blank_to_none(value: Any) -> Any:
    """Normalize empty/whitespace strings to ``None`` for optional fields."""

    if isinstance(value, str) and not value.strip():
        return None
    return value


def coerce_sales(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw sales record to the shape ``SalesPayloadV1`` expects.

    Accepts the common source aliases (``ts`` / ``timestamp`` -> ``order_ts``)
    and coerces ``unit_price`` (currency-as-string) and ``qty`` (numeric string).
    Does not drop unknown keys — ``extra="forbid"`` is the drift detector.
    """

    out = dict(raw)
    # Common timestamp aliases seen from source systems.
    for alias in ("ts", "timestamp", "order_date", "date"):
        if alias in out and "order_ts" not in out:
            out["order_ts"] = out.pop(alias)
    if "order_ts" in out:
        out["order_ts"] = coerce_timestamp(out["order_ts"])
    if "unit_price" in out:
        out["unit_price"] = coerce_float(out["unit_price"])
    if "qty" in out:
        out["qty"] = coerce_int(out["qty"])
    for opt in ("region", "channel", "currency"):
        if opt in out:
            out[opt] = _blank_to_none(out[opt])
    return out


def coerce_ops(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw ops-log record to the shape ``OpsPayloadV1`` expects."""

    out = dict(raw)
    for alias in ("ts", "timestamp", "time"):
        if alias in out and "event_ts" not in out:
            out["event_ts"] = out.pop(alias)
    if "event_ts" in out:
        out["event_ts"] = coerce_timestamp(out["event_ts"])
    if "latency_ms" in out:
        out["latency_ms"] = coerce_float(out["latency_ms"])
    if "status_code" in out:
        out["status_code"] = coerce_int(out["status_code"])
    if "level" in out and isinstance(out["level"], str):
        out["level"] = out["level"].strip().lower() or "info"
    for opt in ("region", "message"):
        if opt in out:
            out[opt] = _blank_to_none(out[opt])
    return out


def coerce_customer(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw customer-activity record to ``CustomerPayloadV1`` shape."""

    out = dict(raw)
    for alias in ("ts", "timestamp", "time"):
        if alias in out and "event_ts" not in out:
            out["event_ts"] = out.pop(alias)
    if "event_ts" in out:
        out["event_ts"] = coerce_timestamp(out["event_ts"])
    for opt in ("region", "channel", "customer_id"):
        if opt in out:
            out[opt] = _blank_to_none(out[opt])
    return out


#: Per-domain coercion dispatch used by the engine.
COERCERS = {
    "sales": coerce_sales,
    "ops": coerce_ops,
    "customer": coerce_customer,
}


def coerce(domain: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Dispatch to the per-domain coercer; unknown domains pass through a copy."""

    coercer = COERCERS.get(domain)
    return coercer(raw) if coercer else dict(raw)
