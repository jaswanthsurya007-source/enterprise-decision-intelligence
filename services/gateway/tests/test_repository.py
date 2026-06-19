"""Unit tests for the in-memory GatewayRepo (tenant scoping, sorting, paging)."""

from __future__ import annotations

import pytest

from edis_gateway.repository import InMemoryGatewayRepo
from gw_testkit import (
    OTHER_TENANT,
    TENANT,
    make_finding,
    make_forecast,
    make_kpi,
    make_recommendation,
)


@pytest.fixture
def seeded() -> InMemoryGatewayRepo:
    return InMemoryGatewayRepo(
        kpis=[make_kpi(), make_kpi(OTHER_TENANT)],
        anomalies=[make_finding(), make_finding(OTHER_TENANT)],
        recommendations=[
            make_recommendation(priority_rank=3, priority_score=0.20),
            make_recommendation(priority_rank=1, priority_score=0.93),
            make_recommendation(priority_rank=2, priority_score=0.55),
            make_recommendation(OTHER_TENANT, priority_rank=1),
        ],
        forecasts=[make_forecast(), make_forecast(OTHER_TENANT)],
    )


@pytest.mark.asyncio
async def test_cross_tenant_read_is_empty(seeded):
    assert await seeded.list_anomalies("nobody") == []
    assert await seeded.list_kpis("nobody") == []


@pytest.mark.asyncio
async def test_tenant_isolation(seeded):
    acme = await seeded.list_recommendations(TENANT)
    assert all(r.tenant_id == TENANT for r in acme)
    assert len(acme) == 3  # the OTHER_TENANT rec is excluded


@pytest.mark.asyncio
async def test_recommendations_priority_order(seeded):
    recs = await seeded.list_recommendations(TENANT)
    assert [r.priority_rank for r in recs] == [1, 2, 3]


@pytest.mark.asyncio
async def test_pagination(seeded):
    page1 = await seeded.list_recommendations(TENANT, limit=2, offset=0)
    page2 = await seeded.list_recommendations(TENANT, limit=2, offset=2)
    assert [r.priority_rank for r in page1] == [1, 2]
    assert [r.priority_rank for r in page2] == [3]


@pytest.mark.asyncio
async def test_status_filter(seeded):
    recs = await seeded.list_recommendations(TENANT, status="accepted")
    assert recs == []  # all seeded are "proposed"
