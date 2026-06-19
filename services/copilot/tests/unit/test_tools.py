"""Unit tests for the four read-only tools (no infra, no keys)."""

from __future__ import annotations

import pytest

from edis_copilot.tools.base import ToolError
from cp_testkit import OTHER_TENANT


async def test_metric_lookup_returns_series_and_numbers(registry, ctx):
    res = await registry.dispatch(
        "metric_lookup",
        ctx,
        metric_key="revenue",
        dimensions={"region": "EMEA", "channel": "web"},
    )
    assert res.tool == "metric_lookup"
    assert len(res.rows) == 8  # 3 pre + 5 drop
    assert 95000.0 in res.numbers and 61000.0 in res.numbers
    assert "revenue" in res.citation


async def test_metric_lookup_daily_rollup_sums_per_day(registry, ctx):
    res = await registry.dispatch(
        "metric_lookup",
        ctx,
        metric_key="revenue",
        dimensions={"region": "EMEA", "channel": "web"},
        rollup="day",
    )
    # One point per day already, so daily sum == per-day value.
    assert all(v in (95000.0, 61000.0) for v in res.numbers)


async def test_metric_lookup_rejects_bad_rollup(registry, ctx):
    with pytest.raises(ToolError):
        await registry.dispatch("metric_lookup", ctx, metric_key="revenue", rollup="hourly")


async def test_metric_lookup_is_tenant_scoped(registry, other_ctx):
    # globex only has one revenue point (999999) — acme's data must not leak.
    res = await registry.dispatch("metric_lookup", other_ctx, metric_key="revenue")
    assert res.numbers == [999999.0]


async def test_structured_query_groups_by_region(registry, ctx):
    res = await registry.dispatch(
        "structured_query",
        ctx,
        metric_key="revenue",
        agg="sum",
        group_by=["region"],
    )
    groups = {tuple(r["group"].values())[0]: r["value"] for r in res.rows}
    assert groups["EMEA"] == pytest.approx(95000.0 * 3 + 61000.0 * 5)
    assert groups["NA"] == pytest.approx(120000.0 * 8)


async def test_structured_query_rejects_unknown_group_by(registry, ctx):
    with pytest.raises(ToolError):
        await registry.dispatch(
            "structured_query",
            ctx,
            metric_key="revenue",
            agg="sum",
            group_by=["customer"],
        )


async def test_structured_query_rejects_bad_agg(registry, ctx):
    with pytest.raises(ToolError):
        await registry.dispatch("structured_query", ctx, metric_key="revenue", agg="median")


async def test_find_anomalies_returns_finding_with_computed_numbers(registry, ctx):
    res = await registry.dispatch("find_anomalies", ctx, metric_key="revenue")
    assert len(res.rows) == 1
    assert res.rows[0]["finding_id"] == "f-7a3"
    # Computed figures + candidate-cause figures are in the grounding whitelist.
    assert 61000.0 in res.numbers and -35.8 in res.numbers
    assert 0.94 in res.numbers and 1220.0 in res.numbers


async def test_find_anomalies_is_tenant_scoped(registry, other_ctx):
    res = await registry.dispatch("find_anomalies", other_ctx, metric_key="revenue")
    assert [r["finding_id"] for r in res.rows] == ["f-other"]


async def test_semantic_search_ranks_relevant_doc_first(registry, ctx):
    res = await registry.dispatch(
        "semantic_search",
        ctx,
        query="why did EMEA revenue drop",
        limit=3,
    )
    assert res.rows, "expected at least one retrieved doc"
    assert res.rows[0]["id"] == "f-7a3"  # the on-topic finding ranks first
    assert 61000.0 in res.numbers


async def test_semantic_search_kind_filter(registry, ctx):
    res = await registry.dispatch(
        "semantic_search",
        ctx,
        query="mitigate checkout latency",
        kinds=["recommendation"],
    )
    assert all(r["kind"] == "recommendation" for r in res.rows)
    assert res.rows[0]["id"] == "r-91c"


async def test_semantic_search_is_tenant_scoped(registry, ctx, other_ctx):
    acme = await registry.dispatch("semantic_search", ctx, query="revenue drop", limit=10)
    globex = await registry.dispatch("semantic_search", other_ctx, query="revenue drop", limit=10)
    acme_ids = {r["id"] for r in acme.rows}
    globex_ids = {r["id"] for r in globex.rows}
    assert "f-globex" not in acme_ids
    assert acme_ids.isdisjoint(globex_ids)


async def test_tenant_arg_is_ignored_not_honored(registry, ctx):
    # Even if the model smuggles a tenant_id kwarg, the tool reads tenant from ctx
    # ONLY — the stray kwarg is silently ignored and can never cross tenants. The
    # result is acme's data (8 EMEA+NA-less filtered rows), never globex's 999999.
    res = await registry.get("metric_lookup").run(
        ctx,
        metric_key="revenue",
        dimensions={"region": "EMEA", "channel": "web"},
        tenant_id=OTHER_TENANT,
    )
    assert 999999.0 not in res.numbers
    assert 61000.0 in res.numbers  # acme's data, scoped by ctx
