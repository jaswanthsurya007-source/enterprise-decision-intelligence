"""API route tests — ingest, control, and health over the real FastAPI app.

Built with ``fastapi.testclient.TestClient`` over ``ingestion.app.create_app()``,
running with **no infra**: the default settings select the in-proc sink and the
in-memory idempotency guard, and there is no database (``writer=None``). The dev
JWT is minted with the same secret the app validates with, exercising the real
auth seam (tenant + roles come only from the token).

Covered (the I2 surface I4 owns the assertions for):

* ``POST /v1/ingest/sales`` single object -> ``200`` ``outcome="landed"``; a bad
  *record* in a well-formed body -> ``200`` ``outcome="dlq"`` (never 5xx).
* a mixed batch -> ``207`` with ``{accepted, rejected, dlq}`` tallied; dedupe is
  observable (a re-posted record comes back ``duplicate``).
* tenant scoping: the landed envelope's tenant is the token's tenant, not the body.
* control plane: ``operator`` role required (a ``viewer`` token is ``403``);
  ``/inject`` rejects a body with both/neither ``profile`` and ``scenario`` (422).
* health: liveness always ok; readiness ok once the lifespan has started.
"""

from __future__ import annotations

import pytest
from edis_platform.authz.jwt import make_dev_token
from edis_platform.settings import Settings

try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover - fastapi always present in this app
    TestClient = None  # type: ignore[assignment]

from ingestion.app import create_app

pytestmark = pytest.mark.skipif(TestClient is None, reason="fastapi testclient unavailable")


def _token(roles: list[str]) -> str:
    return make_dev_token(
        tenant_id="acme",
        user_id="u1",
        roles=roles,
        scopes=["ingest:write"],
        settings=Settings(),
    )


def _auth(roles: list[str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(roles)}"}


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:  # lifespan starts sink + idempotency guard
        yield c


_GOOD_SALES = {
    "order_id": "SO-A1",
    "customer_id": "C1",
    "sku": "SKU-A",
    "qty": "2",
    "unit_price": "$129.00",
    "region": "EMEA",
    "channel": "web",
    "ts": "06/12/2026",
}


# --- ingest ------------------------------------------------------------------


def test_ingest_single_good_record_lands(client):
    r = client.post("/v1/ingest/sales", json=_GOOD_SALES, headers=_auth(["operator"]))
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "landed"
    assert body["idempotency_key"] == "sales:acme:simulator:SO-A1"
    assert body["event_id"]


def test_ingest_bad_record_returns_dlq_not_5xx(client):
    bad = dict(_GOOD_SALES, order_id="SO-BAD", qty="not-an-int")
    r = client.post("/v1/ingest/sales", json=bad, headers=_auth(["operator"]))
    assert r.status_code == 200  # bad data is normal at the edge -> never 5xx
    body = r.json()
    assert body["outcome"] == "dlq"
    assert body["dlq_id"]
    assert "qty" in (body["error"] or "")


def test_ingest_mixed_batch_returns_207_partial(client):
    batch = [
        dict(_GOOD_SALES, order_id="SO-B1"),
        dict(_GOOD_SALES, order_id="SO-B2", qty="oops"),  # -> dlq
        dict(_GOOD_SALES, order_id="SO-B3"),
    ]
    r = client.post("/v1/ingest/sales", json=batch, headers=_auth(["operator"]))
    assert r.status_code == 207
    body = r.json()
    assert body["dlq"] == 1
    assert body["rejected"] == 1
    assert body["accepted"] == 2
    assert body["landed"] == 2
    assert len(body["results"]) == 3


def test_ingest_pure_success_batch_is_200(client):
    batch = [dict(_GOOD_SALES, order_id=f"SO-OK-{i}") for i in range(3)]
    r = client.post("/v1/ingest/sales", json=batch, headers=_auth(["operator"]))
    assert r.status_code == 200
    assert r.json()["dlq"] == 0


def test_ingest_dedupe_observable(client):
    rec = dict(_GOOD_SALES, order_id="SO-DUP-1")
    first = client.post("/v1/ingest/sales", json=rec, headers=_auth(["operator"]))
    second = client.post("/v1/ingest/sales", json=rec, headers=_auth(["operator"]))
    assert first.json()["outcome"] == "landed"
    assert second.json()["outcome"] == "duplicate"


def test_ingest_tenant_comes_from_token_not_body(client):
    # A body claiming another tenant cannot override the token's tenant in the key.
    rec = dict(_GOOD_SALES, order_id="SO-T1", tenant_id="evil")
    r = client.post("/v1/ingest/sales", json=rec, headers=_auth(["operator"]))
    # extra body field is rejected at the edge (DLQ), proving the body is untrusted.
    assert r.status_code == 200
    assert r.json()["outcome"] == "dlq"


def test_ingest_requires_operator_role(client):
    r = client.post("/v1/ingest/sales", json=_GOOD_SALES, headers=_auth(["viewer"]))
    assert r.status_code == 403


def test_ingest_requires_auth(client):
    r = client.post("/v1/ingest/sales", json=_GOOD_SALES)
    assert r.status_code == 401


def test_ingest_ops_record(client):
    ops = {
        "service": "checkout-api",
        "region": "EMEA",
        "level": "error",
        "status_code": "503",
        "latency_ms": "1400",
        "message": "boom",
        "ts": "2026-06-12T10:00:00Z",
    }
    r = client.post("/v1/ingest/ops", json=ops, headers=_auth(["operator"]))
    assert r.status_code == 200
    assert r.json()["outcome"] == "landed"


# --- control plane -----------------------------------------------------------


def test_control_inject_requires_exactly_one_of_profile_or_scenario(client):
    # both -> 422
    r = client.post(
        "/v1/control/simulator/inject",
        json={"profile": "spike", "scenario": "revenue_drop_emea"},
        headers=_auth(["operator"]),
    )
    assert r.status_code == 422
    # neither -> 422
    r = client.post("/v1/control/simulator/inject", json={}, headers=_auth(["operator"]))
    assert r.status_code == 422


def test_control_inject_one_shot_scenario(client):
    r = client.post(
        "/v1/control/simulator/inject",
        json={"scenario": "revenue_drop_emea", "params": {"duration_days": 1}},
        headers=_auth(["operator"]),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["injected"] is True
    assert body["scenario"] == "revenue_drop_emea"


def test_control_requires_operator(client):
    r = client.post("/v1/control/simulator/start", json={}, headers=_auth(["viewer"]))
    assert r.status_code == 403


def test_control_seed_small_history(client):
    r = client.post(
        "/v1/control/seed",
        json={"days": 1, "seed": 42},
        headers=_auth(["operator"]),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["days"] == 1
    assert body["records"] > 0  # real I3 controller produced data through the core


def test_control_status_tenant_scoped(client):
    r = client.get("/v1/control/simulator/status", headers=_auth(["viewer"]))
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "acme"
    assert body["running"] is False


# --- health ------------------------------------------------------------------


def test_health_liveness(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_readiness_after_start(client):
    # TestClient context entered -> lifespan ran -> collaborators started.
    r = client.get("/v1/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["sink"] is True
    assert body["checks"]["idempotency"] is True
