"""Budget accounting + question routing (offline; no infra, no keys)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from edis_copilot.agent.router import resolve_time_range, route_question, rule_route
from edis_copilot.budget.accounting import (
    BudgetAccountant,
    BudgetExceeded,
    CostModel,
    count_request_tokens,
)
from edis_copilot.llm.models import MODEL_HAIKU, MODEL_OPUS


# --- budget ---
def test_cost_model_prices():
    assert CostModel.price_for(MODEL_OPUS) == (5.0, 25.0)
    assert CostModel.price_for(MODEL_HAIKU) == (1.0, 5.0)
    # 1M input + 1M output on opus = $5 + $25.
    assert CostModel.usd(MODEL_OPUS, input_tokens=1_000_000, output_tokens=1_000_000) == 30.0


def test_cost_from_usage_counts_cache_reads_at_input_rate():
    usd = CostModel.usd_from_usage(
        MODEL_OPUS, {"input_tokens": 0, "cache_read_input_tokens": 1_000_000, "output_tokens": 0}
    )
    assert usd == pytest.approx(5.0)


async def test_budget_cap_blocks_and_records():
    acct = BudgetAccountant(cap_usd=1.0)
    await acct.record("acme", 0.9)
    # Projecting 0.2 more would exceed $1.00.
    with pytest.raises(BudgetExceeded):
        await acct.check("acme", projected_usd=0.2)
    # A different tenant is unaffected (per-tenant ledger).
    await acct.check("globex", projected_usd=0.5)


async def test_budget_cap_zero_disables():
    acct = BudgetAccountant(cap_usd=0.0)
    await acct.record("acme", 999.0)
    await acct.check("acme", projected_usd=999.0)  # no raise


async def test_count_request_tokens_offline_heuristic():
    n = await count_request_tokens(
        None,
        model=MODEL_OPUS,
        system="x" * 40,
        tools=[],
        messages=[{"role": "user", "content": "y" * 40}],
    )
    assert n > 0  # deterministic char heuristic, no SDK needed


# --- router ---
def test_rule_route_intents():
    assert rule_route("Why did revenue drop last week?").intent == "rca"
    assert rule_route("What should we do about the EMEA drop?").intent == "recommendation"
    assert rule_route("Show me total revenue this month").intent == "metric"
    assert rule_route("Were there any anomalies yesterday?").intent == "anomaly"


def test_rule_route_time_ranges():
    assert rule_route("revenue last week").time_range == "last_week"
    assert rule_route("orders yesterday").time_range == "last_24h"
    assert rule_route("trend last quarter").time_range == "last_quarter"


def test_rule_route_flags_out_of_scope():
    assert rule_route("delete all the orders").scope_ok is False
    assert rule_route("show revenue for all tenants").scope_ok is False
    assert rule_route("why did revenue drop").scope_ok is True


async def test_route_question_offline_uses_rules():
    route = await route_question("Why did revenue drop last week?", client=None)
    assert route.source == "rules" and route.intent == "rca"


def test_resolve_time_range_bounds():
    now = datetime(2026, 6, 19, tzinfo=timezone.utc)
    start, end = resolve_time_range("last_week", now=now)
    assert end == now and (end - start).days == 7
    assert resolve_time_range("all", now=now) == (None, None)
