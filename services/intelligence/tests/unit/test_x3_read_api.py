"""X3 unit tests — the tenant-scoped read API over the bare app (in-memory repo).

Builds the real FastAPI app (which boots with no DB / no key), seeds the in-memory
repo on ``app.state``, and exercises ``GET /v1/findings`` / ``/v1/findings/{id}`` /
``/v1/forecasts`` with a dev JWT — asserting tenant isolation comes from the token.
Infra-free; uses Starlette's TestClient.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from edis_contracts.findings import Finding, Forecast
from edis_platform.authz.jwt import make_dev_token
from edis_platform.settings import get_settings


def _client_and_token():
    from starlette.testclient import TestClient

    from edis_intelligence.app import create_app

    app = create_app()
    client = TestClient(app)
    settings = get_settings()
    token = make_dev_token("acme", "u1", ["analyst"], [], settings)
    return app, client, token


def _finding(tenant: str, metric: str = "revenue", status: str = "open") -> Finding:
    now = datetime(2026, 6, 19, tzinfo=timezone.utc)
    return Finding(
        finding_id=uuid4(),
        tenant_id=tenant,
        kind="level_shift",
        metric_key=metric,
        dimensions={"region": "EMEA"},
        window_start=now,
        window_end=now,
        detector="stl_seasonal",
        detector_version="1.0",
        observed_value=61000.0,
        expected_value=95000.0,
        deviation=-34000.0,
        deviation_pct=-35.8,
        score=-5.8,
        severity=0.8,
        confidence=0.7,
        business_impact_input=0.6,
        narrative="Revenue fell to 61000.",
        narrative_model=None,
        status=status,
        created_at=now,
    )


def _forecast(tenant: str) -> Forecast:
    return Forecast(
        forecast_id=uuid4(),
        tenant_id=tenant,
        metric_key="revenue",
        dimensions={"region": "EMEA"},
        model="statsmodels.ETS",
        horizon_days=7,
        points=[
            {
                "ts": "2026-06-20T00:00:00+00:00",
                "yhat": 60000.0,
                "yhat_lower": 50000.0,
                "yhat_upper": 70000.0,
            }
        ],
        generated_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
    )


def test_health_is_open() -> None:
    _app, client, _token = _client_and_token()
    r = client.get("/v1/findings/../health".replace("/findings/..", ""))  # /v1/health
    assert r.status_code == 200
    assert r.json()["service"] == "edis-intelligence"


@pytest.mark.asyncio
async def test_list_and_get_findings_tenant_scoped() -> None:
    app, client, token = _client_and_token()
    repo = app.state.repo
    f_acme = _finding("acme")
    await repo.save_finding(f_acme)
    await repo.save_finding(_finding("acme", metric="error_rate"))
    await repo.save_finding(_finding("ghost"))  # other tenant

    auth = {"Authorization": f"Bearer {token}"}

    r = client.get("/v1/findings", headers=auth)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2  # only acme's
    assert all(row["tenant_id"] == "acme" for row in rows)

    r = client.get(f"/v1/findings/{f_acme.finding_id}", headers=auth)
    assert r.status_code == 200
    assert r.json()["finding_id"] == str(f_acme.finding_id)

    # metric_key filter
    r = client.get("/v1/findings?metric_key=error_rate", headers=auth)
    assert {row["metric_key"] for row in r.json()} == {"error_rate"}


@pytest.mark.asyncio
async def test_get_finding_other_tenant_is_404() -> None:
    app, client, token = _client_and_token()
    ghost = _finding("ghost")
    await app.state.repo.save_finding(ghost)
    r = client.get(f"/v1/findings/{ghost.finding_id}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")


def test_findings_requires_auth() -> None:
    _app, client, _token = _client_and_token()
    r = client.get("/v1/findings")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_forecasts_tenant_scoped() -> None:
    app, client, token = _client_and_token()
    await app.state.repo.save_forecast(_forecast("acme"))
    await app.state.repo.save_forecast(_forecast("ghost"))
    r = client.get("/v1/forecasts", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "acme"


def test_bare_app_uses_in_memory_repo_and_stub_embedder() -> None:
    from edis_intelligence.store.repositories import InMemoryIntelligenceRepo

    app, _client, _token = _client_and_token()
    assert isinstance(app.state.repo, InMemoryIntelligenceRepo)
    # no key -> no narration client, stub embedder
    assert app.state.narration_client is None
    assert app.state.embedder.model == "stub-hash-1024"
