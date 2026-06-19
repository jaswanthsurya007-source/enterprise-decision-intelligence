"""Read-only, tenant-scoped copilot tools and the frozen Anthropic tool registry.

The four tools — :class:`~app.tools.metric_lookup.MetricLookupTool`,
:class:`~app.tools.structured_query.StructuredQueryTool`,
:class:`~app.tools.finding_retrieval.FindAnomaliesTool`, and
:class:`~app.tools.semantic_search.SemanticSearchTool` — are all strictly
read-only and take ``tenant_id`` from the injected :class:`~app.tools.base.ToolContext`
(derived from the verified JWT) **server-side**. The LLM can never set or change the
tenant, and retrieved content is data, never instructions.

:func:`~app.tools.registry.default_registry` exposes them in a FROZEN, deterministic
order so the Anthropic tool-schema list forms a stable prompt-cache prefix.
"""

from __future__ import annotations

from edis_copilot.tools.base import (
    DataPort,
    InMemoryDataPort,
    Tool,
    ToolContext,
    ToolError,
    ToolResult,
)
from edis_copilot.tools.finding_retrieval import FindAnomaliesTool
from edis_copilot.tools.metric_lookup import MetricLookupTool
from edis_copilot.tools.registry import ToolRegistry, default_registry
from edis_copilot.tools.semantic_search import SemanticSearchTool
from edis_copilot.tools.structured_query import StructuredQueryTool

__all__ = [
    "DataPort",
    "InMemoryDataPort",
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolResult",
    "ToolRegistry",
    "default_registry",
    "MetricLookupTool",
    "StructuredQueryTool",
    "FindAnomaliesTool",
    "SemanticSearchTool",
]
