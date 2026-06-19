"""P3 — tenant injection is server-side; a model-supplied tenant can never cross over.

The copilot security pin: every read-only tool scopes to ``ctx.tenant_id`` (from the
verified SecurityContext) and NOTHING the model puts in the tool arguments — a
``tenant_id``/``tenant`` kwarg, a smuggled dimension — can widen or change that scope.
These tests drive the tools directly (no LLM) so the isolation boundary is proven at the
tool layer itself, where it is load-bearing.
"""

from __future__ import annotations

import pytest

from edis_copilot.tools.base import ToolContext
from cp_testkit import OTHER_TENANT, TENANT


async def test_tool_ignores_tenant_kwarg_and_uses_ctx(registry, ctx):
    """A ``tenant_id`` kwarg pointed at another tenant is ignored; ctx tenant wins.

    The tool returns acme's EMEA series (61000/95000), never globex's lone 999999 point.
    """

    res = await registry.get("metric_lookup").run(
        ctx,
        metric_key="revenue",
        dimensions={"region": "EMEA", "channel": "web"},
        tenant_id=OTHER_TENANT,  # the model tries to smuggle a cross-tenant scope
    )
    assert 61000.0 in res.numbers and 95000.0 in res.numbers
    assert 999999.0 not in res.numbers  # globex's data never leaks


async def test_tool_ignores_tenant_alias_kwarg(registry, ctx):
    """The ``tenant`` alias (not just ``tenant_id``) is likewise inert."""

    res = await registry.get("find_anomalies").run(ctx, metric_key="revenue", tenant="globex")
    # acme's finding surfaced; the other tenant's f-other is never returned.
    assert [r["finding_id"] for r in res.rows] == ["f-7a3"]


async def test_semantic_search_tenant_kwarg_does_not_cross(registry, ctx):
    """semantic_search keyed with a foreign ``tenant_id`` still searches the ctx tenant."""

    res = await registry.get("semantic_search").run(
        ctx, query="why did EMEA revenue drop", limit=10, tenant_id=OTHER_TENANT
    )
    ids = {r["id"] for r in res.rows}
    assert "f-7a3" in ids  # acme's on-topic finding
    assert "f-globex" not in ids  # globex's identically-embedded doc never retrieved


async def test_each_context_sees_only_its_own_tenant(registry, ctx, other_ctx):
    """Two contexts over the SAME registry/data each see strictly their own tenant.

    Same query, same seeded port — the only difference is the SecurityContext, and that
    alone partitions the results. No row from one tenant appears for the other.
    """

    acme = await registry.dispatch("metric_lookup", ctx, metric_key="revenue")
    globex = await registry.dispatch("metric_lookup", other_ctx, metric_key="revenue")
    assert 61000.0 in acme.numbers and 999999.0 not in acme.numbers
    assert globex.numbers == [999999.0]  # globex's only seeded point


async def test_context_tenant_comes_from_security_and_is_frozen():
    """``ToolContext.tenant_id`` is exactly the SecurityContext tenant; ctx is immutable."""

    import dataclasses

    ctx = ToolContext.for_tenant(TENANT)
    assert ctx.tenant_id == TENANT
    # The context is a frozen dataclass — the verified security principal it carries
    # cannot be swapped out for a different-tenant one at runtime.
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.security = ToolContext.for_tenant(OTHER_TENANT).security  # type: ignore[misc]
