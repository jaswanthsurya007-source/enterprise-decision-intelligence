"""Unit tests for the tenant-scoped REST snapshot routes (no infra)."""

from __future__ import annotations

import pytest

from gw_testkit import OTHER_TENANT, TENANT


def test_health_is_unauthenticated(client):
    res = client.get("/v1/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_kpis_requires_auth(client):
    res = client.get("/v1/kpis")
    assert res.status_code == 401
    assert res.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize(
    "path",
    ["/v1/kpis", "/v1/anomalies", "/v1/recommendations", "/v1/forecasts"],
)
def test_reads_are_tenant_scoped(client, auth, path):
    res = client.get(path, headers=auth)
    assert res.status_code == 200
    rows = res.json()
    assert rows, f"{path} returned nothing"
    for row in rows:
        # KPI/finding/rec/forecast all carry tenant_id; none may belong to OTHER_TENANT.
        assert row["tenant_id"] == TENANT
        assert row["tenant_id"] != OTHER_TENANT


def test_recommendations_sorted_by_priority(client, auth):
    res = client.get("/v1/recommendations", headers=auth)
    assert res.status_code == 200
    ranks = [r["priority_rank"] for r in res.json()]
    assert ranks == sorted(ranks), "recommendations must be priority-ordered (best rank first)"
    assert ranks[0] == 1


def test_anomalies_response_is_finding_shape(client, auth):
    res = client.get("/v1/anomalies", headers=auth)
    assert res.status_code == 200
    finding = res.json()[0]
    # Canonical edis.findings.v1 fields are present and computed values pass through.
    for key in ("finding_id", "kind", "observed_value", "expected_value", "deviation_pct"):
        assert key in finding
    assert finding["observed_value"] == 61000.0


def test_kpi_carries_wow_delta(client, auth):
    res = client.get("/v1/kpis", headers=auth)
    assert res.status_code == 200
    kpi = res.json()[0]
    assert kpi["delta_pct"] == -8.3
    assert kpi["previous_value"] == 420000.0


def test_metric_key_filter(client, auth):
    res = client.get("/v1/anomalies?metric_key=does-not-exist", headers=auth)
    assert res.status_code == 200
    assert res.json() == []


def test_viewer_can_read_but_cannot_query_copilot(client, viewer_token):
    headers = {"Authorization": f"Bearer {viewer_token}"}
    assert client.get("/v1/kpis", headers=headers).status_code == 200
    # The proxy enforces AI_QUERY; a viewer is forbidden at the edge.
    res = client.post("/v1/copilot/chat", headers=headers, content=b"{}")
    assert res.status_code == 403
    assert res.headers["content-type"].startswith("application/problem+json")


def test_limit_validation(client, auth):
    res = client.get("/v1/anomalies?limit=0", headers=auth)
    assert res.status_code == 422
