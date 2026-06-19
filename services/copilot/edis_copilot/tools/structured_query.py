"""``structured_query`` — safe, parameterized metric aggregates. NO raw SQL.

The model never writes SQL. It picks a metric, an aggregate from a fixed enum, an
optional dimension filter, and optional ``group_by`` dimension keys; the DataPort
turns that into a parameterized aggregate. ``tenant_id`` is injected from the
:class:`~app.tools.base.ToolContext` server-side. This is the only "compute" tool and
it is strictly bounded — there is no free-text query surface for prompt injection to
target.
"""

from __future__ import annotations

from typing import Any

from edis_copilot.tools.base import DataPort, Tool, ToolContext, ToolError, ToolResult
from edis_copilot.tools.metric_lookup import _opt_dims, _opt_dt, _require_str

_VALID_AGGS = ("sum", "avg", "min", "max", "count")
#: The only dimension keys the model may group by / filter on (canonical model dims).
_ALLOWED_DIMS = ("region", "channel", "service")


class StructuredQueryTool(Tool):
    """Run a safe parameterized aggregate over a metric (no raw SQL from the model)."""

    name = "structured_query"
    description = (
        "Compute a safe aggregate (sum/avg/min/max/count) of one metric for the "
        "current tenant, optionally filtered and grouped by region/channel/service. "
        "Call this for 'what is the total/average/breakdown by region' style questions "
        "instead of summing points yourself. You choose the metric, aggregate, and "
        "grouping from fixed options — you cannot write SQL. Use only the returned "
        "values in your answer."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "metric_key": {
                "type": "string",
                "description": "Metric to aggregate, e.g. 'revenue', 'orders', 'error_rate'.",
            },
            "agg": {
                "type": "string",
                "enum": list(_VALID_AGGS),
                "description": "Aggregate function to apply.",
            },
            "dimensions": {
                "type": "object",
                "description": "Optional equality filter, e.g. {'region':'EMEA'}. "
                "Keys limited to region/channel/service.",
                "additionalProperties": {"type": "string"},
            },
            "group_by": {
                "type": "array",
                "items": {"type": "string", "enum": list(_ALLOWED_DIMS)},
                "description": "Optional dimension keys to group by (region/channel/service).",
            },
            "start": {"type": "string", "description": "Optional ISO-8601 start (inclusive), UTC."},
            "end": {"type": "string", "description": "Optional ISO-8601 end (inclusive), UTC."},
        },
        "required": ["metric_key", "agg"],
        "additionalProperties": False,
    }

    def __init__(self, data: DataPort) -> None:
        self._data = data

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        metric_key = _require_str(kwargs, "metric_key")
        agg = _norm_agg(kwargs.get("agg"))
        dimensions = _validate_dims(_opt_dims(kwargs.get("dimensions")))
        group_by = _validate_group_by(kwargs.get("group_by"))
        start = _opt_dt(kwargs.get("start"), "start")
        end = _opt_dt(kwargs.get("end"), "end")

        rows = await self._data.metric_aggregate(
            ctx.tenant_id,
            metric_key,
            agg=agg,
            start=start,
            end=end,
            dimensions=dimensions,
            group_by=group_by,
        )
        numbers = [float(r["value"]) for r in rows if "value" in r]
        gb_str = f" by {', '.join(group_by)}" if group_by else ""
        return ToolResult(
            tool=self.name,
            rows=rows,
            numbers=numbers,
            citation=f"tool {self.name}: {agg}({metric_key}){gb_str}",
            summary=f"{agg} of {metric_key}{gb_str}: {len(rows)} group(s).",
        )


def _norm_agg(v: Any) -> str:
    s = str(v or "").lower()
    if s not in _VALID_AGGS:
        raise ToolError(f"agg must be one of {'|'.join(_VALID_AGGS)} (got {v!r})")
    return s


def _validate_dims(dims: dict[str, str] | None) -> dict[str, str] | None:
    if not dims:
        return dims
    bad = [k for k in dims if k not in _ALLOWED_DIMS]
    if bad:
        raise ToolError(f"dimension keys must be one of {_ALLOWED_DIMS} (got {bad})")
    return dims


def _validate_group_by(v: Any) -> list[str] | None:
    if v is None:
        return None
    if not isinstance(v, list):
        raise ToolError("group_by must be an array of dimension keys")
    keys = [str(k) for k in v]
    bad = [k for k in keys if k not in _ALLOWED_DIMS]
    if bad:
        raise ToolError(f"group_by keys must be one of {_ALLOWED_DIMS} (got {bad})")
    return keys
