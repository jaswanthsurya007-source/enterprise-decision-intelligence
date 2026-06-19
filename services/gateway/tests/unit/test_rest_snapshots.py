"""P3 — gateway REST snapshot routes: tenant-scoped, priority-sorted, auth-gated.

Drives the real gateway app (built in ``tests/conftest.py``) over the in-memory repo +
dev JWT — no Docker, no broker, no keys. Covers the four read snapshots
(/v1/kpis, /v1/anomalies, /v1/recommendations, /v1/forecasts): every row is scoped to the
verified tenant (the cross-tenant rows the repo also holds are never returned),
recommendations come back priority-sorted (best rank first), and an unauthenticated read
is rejected with an RFC 9457 problem response.
"""

from __future__ import annotations

import pytest

from gw_testkit import OTHER_TENANT, TENANT

_SNAPSHOT_PATHS = ["/v1/kpis", "/v1/anomalies", "/v1/recommendations", "/v1/forecasts"]


@pytest.mark.parametrize("path", _SNAPSHOT_PATHS)
def test_snapshot_requires_auth(client, path):
    """No bearer JWT -> 401 with an RFC 9457 problem+json body (tenant must be verified)."""

    res = client.get(path)
    assert res.status_code == 401
    assert res.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize("path", _SNAPSHOT_PATHS)
def test_snapshot_is_tenant_scoped(client, auth, path):
    """Each snapshot returns only the verified tenant's rows — never the cross-tenant seed."""

    res = client.get(path, headers=auth)
    assert res.status_code == 200
    rows = res.json()
    assert rows, f"{path} returned no rows for the seeded tenant"
    for row in rows:
        assert row["tenant_id"] == TENANT
        assert row["tenant_id"] != OTHER_TENANT


def test_recommendations_priority_sorted(client, auth):
    """Recommendations are ordered by priority (rank 1 first), regardless of seed order."""

    res = client.get("/v1/recommendations", headers=auth)
    assert res.status_code == 200
    ranks = [r["priority_rank"] for r in res.json()]
    assert ranks == sorted(ranks)
    assert ranks[0] == 1  # the highest-priority recommendation leads


def test_kpi_snapshot_carries_wow_delta(client, auth):
    """KPI tiles carry the week-over-week delta straight from the L2 rollup."""

    kpi = client.get("/v1/kpis", headers=auth).json()[0]
    assert kpi["delta_pct"] == -8.3
    assert kpi["previous_value"] == 420000.0


def test_anomalies_carry_computed_finding_values(client, auth):
    """Anomaly rows are canonical Finding shape with the computed (not LLM) values."""

    finding = client.get("/v1/anomalies", headers=auth).json()[0]
    for key in ("finding_id", "kind", "observed_value", "expected_value", "deviation_pct"):
        assert key in finding
    assert finding["observed_value"] == 61000.0
    assert finding["deviation_pct"] == -35.8


def test_metric_key_filter_returns_empty_for_unknown(client, auth):
    """An unknown ``metric_key`` filter yields an empty (still tenant-scoped) list."""

    res = client.get("/v1/anomalies?metric_key=nope", headers=auth)
    assert res.status_code == 200
    assert res.json() == []
