"""The tool registry — a FROZEN, deterministic tool order for prompt-cache stability.

The Anthropic ``tools`` array renders at position 0 of the request, before ``system``
and ``messages``. Any change to that array (reorder, add, remove) invalidates the
entire prompt cache. So the registry exposes the tools in a **single frozen order**
(:data:`FROZEN_TOOL_ORDER`) and :meth:`ToolRegistry.anthropic_tools` always renders
them in that order with the same bytes — making the tool schemas + frozen system
prefix a stable, cacheable prefix (cache effectiveness is asserted via
``usage.cache_read_input_tokens > 0`` after warm-up by P2).

The registry also enforces the security invariant that **no tool's input schema
declares a ``tenant_id`` property** — the tenant is injected server-side from the
:class:`~app.tools.base.ToolContext`, so it can never be a model-supplied argument.
"""

from __future__ import annotations

from typing import Any

from edis_copilot.tools.base import Tool, ToolContext, ToolError, ToolResult

#: The single source of truth for tool ordering. NEVER reorder casually — a change
#: invalidates the prompt cache for every copilot turn. New tools append at the end.
FROZEN_TOOL_ORDER: tuple[str, ...] = (
    "metric_lookup",
    "structured_query",
    "find_anomalies",
    "semantic_search",
)


class ToolRegistry:
    """An ordered, name-indexed collection of read-only tools.

    Construct with the tools (any order); the registry sorts them into
    :data:`FROZEN_TOOL_ORDER`. :meth:`anthropic_tools` returns the deterministic schema
    list; :meth:`dispatch` runs a tool by name with the server-side context.
    """

    def __init__(self, tools: list[Tool]) -> None:
        by_name: dict[str, Tool] = {}
        for tool in tools:
            if not tool.name:
                raise ValueError("every tool must declare a non-empty name")
            if tool.name in by_name:
                raise ValueError(f"duplicate tool name: {tool.name!r}")
            _assert_no_tenant_arg(tool)
            by_name[tool.name] = tool
        # Freeze into the canonical order; unknown names (not in the frozen list)
        # sort to the end alphabetically so the order stays fully deterministic.
        order = {name: i for i, name in enumerate(FROZEN_TOOL_ORDER)}
        self._tools: list[Tool] = sorted(
            by_name.values(),
            key=lambda t: (order.get(t.name, len(order)), t.name),
        )
        self._by_name = {t.name: t for t in self._tools}

    @property
    def names(self) -> list[str]:
        """Tool names in frozen order."""

        return [t.name for t in self._tools]

    def get(self, name: str) -> Tool:
        """Return the tool named ``name`` or raise :class:`ToolError`."""

        tool = self._by_name.get(name)
        if tool is None:
            raise ToolError(f"unknown tool: {name!r}")
        return tool

    def anthropic_tools(self) -> list[dict[str, Any]]:
        """Return the Anthropic ``tools`` array in the FROZEN, deterministic order.

        Byte-stable across requests (no timestamps/ids), so it forms a cacheable
        prompt prefix together with the frozen system prompt.
        """

        return [t.anthropic_schema() for t in self._tools]

    async def dispatch(self, name: str, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        """Run tool ``name`` with the server-side ``ctx`` and model-supplied kwargs.

        The tenant is taken from ``ctx`` inside the tool; ``kwargs`` are the model's
        tool input (never a tenant). Raises :class:`ToolError` for unknown tools or
        caller-correctable argument problems.
        """

        return await self.get(name).run(ctx, **kwargs)


def _assert_no_tenant_arg(tool: Tool) -> None:
    """Fail fast if a tool's input schema exposes a tenant field to the model."""

    props = (tool.input_schema or {}).get("properties", {})
    forbidden = {"tenant_id", "tenant"}
    leaked = forbidden.intersection(props)
    if leaked:
        raise ValueError(
            f"tool {tool.name!r} must not expose {sorted(leaked)} in its input schema; "
            "tenant_id is injected server-side from the SecurityContext"
        )


def default_registry(
    *,
    data,
    searcher,
    max_tool_rows: int = 200,
    semantic_k: int = 8,
) -> ToolRegistry:
    """Build the standard four-tool registry in the frozen order.

    ``data`` is a :class:`~app.tools.base.DataPort` (the InMemory fake or the
    SQLAlchemy repo); ``searcher`` is a :class:`~app.retrieval.search.HybridSearcher`
    wired to the same data + a key-guarded embedder. Importing/building this needs no
    infrastructure and no API keys when the InMemory port + stub embedder are used.
    """

    from edis_copilot.tools.finding_retrieval import FindAnomaliesTool
    from edis_copilot.tools.metric_lookup import MetricLookupTool
    from edis_copilot.tools.semantic_search import SemanticSearchTool
    from edis_copilot.tools.structured_query import StructuredQueryTool

    return ToolRegistry(
        [
            MetricLookupTool(data, max_rows=max_tool_rows),
            StructuredQueryTool(data),
            FindAnomaliesTool(data, max_rows=max_tool_rows),
            SemanticSearchTool(searcher, default_limit=semantic_k, max_rows=max_tool_rows),
        ]
    )
