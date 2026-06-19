"""``find_anomalies`` — retrieve findings (detections) for a metric/window.

Read-only. ``tenant_id`` is injected from :class:`~app.tools.base.ToolContext`
server-side. Returns the persisted :class:`~edis_contracts.findings.Finding`-shaped
rows the L3 engine produced — observed/expected/deviation, candidate causes, severity,
confidence — which are all computed facts the answer may cite. The flat number list
gathers every figure on each returned finding (and its candidate causes) for the
per-turn grounding whitelist.
"""

from __future__ import annotations

from typing import Any

from edis_copilot.tools.base import DataPort, Tool, ToolContext, ToolResult
from edis_copilot.tools.metric_lookup import _clamp_limit, _opt_dt

_NUMERIC_FINDING_FIELDS = (
    "observed_value",
    "expected_value",
    "deviation",
    "deviation_pct",
    "score",
    "severity",
    "confidence",
    "business_impact_input",
)
_NUMERIC_CAUSE_FIELDS = ("correlation", "lag_minutes", "contribution_pct", "observed_delta")


class FindAnomaliesTool(Tool):
    """Retrieve detected anomalies (findings) for a metric and time window."""

    name = "find_anomalies"
    description = (
        "Retrieve detected anomalies (findings) for the current tenant, optionally "
        "filtered by metric and window. Call this for 'what went wrong / what anomalies "
        "happened' questions and to ground a root-cause answer in the computed finding "
        "(observed vs expected value, deviation, candidate causes). Every number on a "
        "finding is a computed fact — cite those, do not estimate."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "metric_key": {
                "type": "string",
                "description": "Optional metric to filter by, e.g. 'revenue'.",
            },
            "status": {
                "type": "string",
                "enum": ["open", "acknowledged", "resolved", "expired"],
                "description": "Optional finding status filter.",
            },
            "start": {
                "type": "string",
                "description": "Optional ISO-8601 window start (inclusive), UTC.",
            },
            "end": {
                "type": "string",
                "description": "Optional ISO-8601 window end (inclusive), UTC.",
            },
            "limit": {
                "type": "integer",
                "description": "Max findings to return (newest first). Default 25.",
            },
        },
        "required": [],
        "additionalProperties": False,
    }

    def __init__(self, data: DataPort, *, default_limit: int = 25, max_rows: int = 200) -> None:
        self._data = data
        self._default_limit = default_limit
        self._max_rows = max_rows

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        metric_key = kwargs.get("metric_key")
        metric_key = str(metric_key).strip() if metric_key else None
        status = kwargs.get("status")
        status = str(status) if status else None
        start = _opt_dt(kwargs.get("start"), "start")
        end = _opt_dt(kwargs.get("end"), "end")
        limit = _clamp_limit(kwargs.get("limit", self._default_limit), self._max_rows)

        rows = await self._data.findings_for_metric(
            ctx.tenant_id,
            metric_key=metric_key,
            start=start,
            end=end,
            status=status,
            limit=limit,
        )
        numbers = _finding_numbers(rows)
        scope = f" for {metric_key}" if metric_key else ""
        return ToolResult(
            tool=self.name,
            rows=rows,
            numbers=numbers,
            citation=f"tool {self.name}{scope}",
            summary=f"{len(rows)} finding(s){scope}.",
        )


def _finding_numbers(rows: list[dict[str, Any]]) -> list[float]:
    """Flatten every computed figure off the findings + their candidate causes."""

    out: list[float] = []
    for f in rows:
        for key in _NUMERIC_FINDING_FIELDS:
            v = f.get(key)
            if isinstance(v, (int, float)):
                out.append(float(v))
        for cause in f.get("candidate_causes", []) or []:
            if not isinstance(cause, dict):
                continue
            for key in _NUMERIC_CAUSE_FIELDS:
                v = cause.get(key)
                if isinstance(v, (int, float)):
                    out.append(float(v))
    return out
