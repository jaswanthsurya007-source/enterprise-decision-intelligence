"""``metric_lookup`` — fetch a metric series or daily rollup for the tenant.

Read-only. ``tenant_id`` is injected from :class:`~app.tools.base.ToolContext`
server-side and is NOT part of the input schema — the model cannot set it. The tool
clamps ``limit`` and validates the rollup, parses any ISO time bounds, and returns the
raw points (or daily rollups) plus the flat list of their values as the per-turn
grounding whitelist.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from edis_copilot.tools.base import DataPort, Tool, ToolContext, ToolError, ToolResult

_VALID_ROLLUPS = ("raw", "day", "daily", "weekly", "week")


class MetricLookupTool(Tool):
    """Look up a metric time series (or its daily rollup) for the current tenant."""

    name = "metric_lookup"
    description = (
        "Fetch a time series (or daily rollup) for one metric within the current "
        "tenant. Call this when the question is about how a metric (revenue, orders, "
        "error_rate, latency_p95, page_views) moved over time, or to read its current "
        "vs prior values. Returns points with timestamps and values; never invent "
        "values not present in the result."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "metric_key": {
                "type": "string",
                "description": "Metric to read, e.g. 'revenue', 'orders', 'error_rate', "
                "'latency_p95', 'page_views'.",
            },
            "dimensions": {
                "type": "object",
                "description": "Optional dimension filter, e.g. {'region':'EMEA','channel':'web'}.",
                "additionalProperties": {"type": "string"},
            },
            "rollup": {
                "type": "string",
                "enum": ["raw", "day", "weekly"],
                "description": "Aggregation grain: 'raw' points, daily, or weekly rollup.",
            },
            "start": {
                "type": "string",
                "description": "Optional ISO-8601 start (inclusive), UTC.",
            },
            "end": {
                "type": "string",
                "description": "Optional ISO-8601 end (inclusive), UTC.",
            },
            "limit": {
                "type": "integer",
                "description": "Max points to return (most recent). Default 200.",
            },
        },
        "required": ["metric_key"],
        "additionalProperties": False,
    }

    def __init__(self, data: DataPort, *, max_rows: int = 200) -> None:
        self._data = data
        self._max_rows = max_rows

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        metric_key = _require_str(kwargs, "metric_key")
        dimensions = _opt_dims(kwargs.get("dimensions"))
        rollup = _norm_rollup(kwargs.get("rollup", "raw"))
        start = _opt_dt(kwargs.get("start"), "start")
        end = _opt_dt(kwargs.get("end"), "end")
        limit = _clamp_limit(kwargs.get("limit"), self._max_rows)

        rows = await self._data.metric_series(
            ctx.tenant_id,
            metric_key,
            start=start,
            end=end,
            dimensions=dimensions,
            rollup=rollup,
            limit=limit,
        )
        numbers = [float(r["value"]) for r in rows if "value" in r]
        dim_str = (
            " [" + ", ".join(f"{k}={v}" for k, v in sorted(dimensions.items())) + "]"
            if dimensions
            else ""
        )
        return ToolResult(
            tool=self.name,
            rows=rows,
            numbers=numbers,
            citation=f"tool {self.name}: {metric_key}{dim_str} ({rollup})",
            summary=f"{len(rows)} {rollup} point(s) of {metric_key}{dim_str}.",
        )


# --- shared arg helpers (pure) ---
def _require_str(kwargs: dict[str, Any], key: str) -> str:
    v = kwargs.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ToolError(f"{key} is required and must be a non-empty string")
    return v.strip()


def _opt_dims(v: Any) -> dict[str, str] | None:
    if v is None:
        return None
    if not isinstance(v, dict):
        raise ToolError("dimensions must be an object of string->string")
    return {str(k): str(val) for k, val in v.items()}


def _norm_rollup(v: Any) -> str:
    s = str(v or "raw").lower()
    if s not in _VALID_ROLLUPS:
        raise ToolError(f"rollup must be one of raw|day|weekly (got {v!r})")
    if s in ("day", "daily"):
        return "day"
    if s in ("week", "weekly"):
        return "weekly"
    return "raw"


def _opt_dt(v: Any, field: str) -> datetime | None:
    if v is None or v == "":
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolError(f"{field} must be ISO-8601 (got {v!r})") from exc


def _clamp_limit(v: Any, max_rows: int) -> int:
    if v is None:
        return max_rows
    try:
        n = int(v)
    except (TypeError, ValueError) as exc:
        raise ToolError("limit must be an integer") from exc
    return max(1, min(n, max_rows))
