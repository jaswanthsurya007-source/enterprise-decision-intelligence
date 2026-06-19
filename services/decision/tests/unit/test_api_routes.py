"""Unit tests for the decision REST API over ``create_app`` -- NO infra, NO key.

Uses FastAPI's :class:`TestClient` against the real app, with the data-access dependencies
(``get_repository`` / ``get_lifecycle_manager``) overridden to point at an in-memory repo +
fake bus, so the whole API -- list, get, accept (200 + lifecycle), illegal accept-after-
accept (409), and the auth (401/403) gates -- is exercised with no Postgres, no broker, and
no Anthropic key.
"""

from __future__ import annotations

import pytest

from edis_contracts import topics
from edis_platform.errors import PROBLEM_CONTENT_TYPE

from decision_engine.api.deps import get_lifecycle_manager, get_repository
from decision_engine.events.producer import DecisionEventProducer
from decision_engine.lifecycle.manager import LifecycleManager
from decision_engine.main import create_app
from decision_engine.synthesis.synthesizer import synthesize

from edis_l4_testkit import (
    DEMO_NOW,
    DEMO_TENANT,
    FakeSink,
    InMemoryRecommendationRepo,
    build_demo_finding,
)


@pytest.fixture
def app_ctx(no_keys_settings):
    """An app + shared in-memory repo + fake sink, with the data deps overridden.

    Returns ``(app, repo, sink)``. The same repo/sink instances are used by both the
    repository dep and the lifecycle-manager dep, so a transition the manager applies is
    visible to a subsequent GET -- exactly like one DB would behave.
    """

    from fastapi.testclient import TestClient  # noqa: F401  (import-safety check)

    app = create_app()
    repo = InMemoryRecommendationRepo()
    sink = FakeSink()
    # The app's lifespan starts the real make_sink() sink; for the API tests we don't run
    # the lifespan (TestClient context would), and the manager dep uses our fake sink.
    app.state.sink = sink

    async def _repo_override():
        yield repo

    async def _manager_override():
        yield LifecycleManager(repo, DecisionEventProducer(sink), sink)

    app.dependency_overrides[get_repository] = _repo_override
    app.dependency_overrides[get_lifecycle_manager] = _manager_override
    return app, repo, sink


def _client(app):
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=True)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed(repo, **overrides):
    rec = await synthesize(build_demo_finding(), now=DEMO_NOW)
    if overrides:
        rec = rec.model_copy(update=overrides)
    await repo.save_recommendation(rec)
    return rec


# ---------------------------------------------------------------------------
# list / get (read; viewer allowed)
# ---------------------------------------------------------------------------
async def test_list_recommendations_returns_tenant_rows(app_ctx, viewer_token):
    app, repo, _sink = app_ctx
    rec = await _seed(repo)

    with _client(app) as client:
        resp = client.get("/v1/recommendations", headers=_auth(viewer_token))

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["recommendation_id"] == str(rec.recommendation_id)
    assert body[0]["action_type"] == "operational_fix"
    assert body[0]["priority_rank"] == 1


async def test_list_is_tenant_scoped(app_ctx, viewer_token):
    """A viewer for tenant 'acme' never sees another tenant's recommendations."""

    app, repo, _sink = app_ctx
    await _seed(repo)  # acme
    other = await synthesize(
        build_demo_finding(tenant_id="globex", finding_id=build_demo_finding().finding_id),
        now=DEMO_NOW,
    )
    await repo.save_recommendation(other)

    with _client(app) as client:
        resp = client.get("/v1/recommendations", headers=_auth(viewer_token))

    assert resp.status_code == 200
    body = resp.json()
    assert {r["tenant_id"] for r in body} == {DEMO_TENANT}


async def test_get_recommendation_ok(app_ctx, viewer_token):
    app, repo, _sink = app_ctx
    rec = await _seed(repo)

    with _client(app) as client:
        resp = client.get(
            f"/v1/recommendations/{rec.recommendation_id}", headers=_auth(viewer_token)
        )

    assert resp.status_code == 200
    assert resp.json()["recommendation_id"] == str(rec.recommendation_id)


async def test_get_unknown_recommendation_404(app_ctx, viewer_token):
    import uuid

    app, _repo, _sink = app_ctx
    with _client(app) as client:
        resp = client.get(f"/v1/recommendations/{uuid.uuid4()}", headers=_auth(viewer_token))

    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)


# ---------------------------------------------------------------------------
# accept -> 200 + lifecycle ; illegal accept-after-accept -> 409
# ---------------------------------------------------------------------------
async def test_accept_returns_200_and_emits_lifecycle(app_ctx, operator_token):
    app, repo, sink = app_ctx
    rec = await _seed(repo)

    with _client(app) as client:
        resp = client.post(
            f"/v1/recommendations/{rec.recommendation_id}/accept",
            headers=_auth(operator_token),
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"

    # Persisted + lifecycle event published + audit emitted.
    stored = await repo.get(DEMO_TENANT, rec.recommendation_id)
    assert stored.status == "accepted"
    assert len(repo.lifecycle_rows) == 1
    assert topics.DECISIONS_LIFECYCLE in sink.topics_published()
    assert topics.AUDIT in sink.topics_published()


async def test_accept_after_accept_returns_409(app_ctx, operator_token):
    app, repo, _sink = app_ctx
    rec = await _seed(repo)

    with _client(app) as client:
        first = client.post(
            f"/v1/recommendations/{rec.recommendation_id}/accept",
            headers=_auth(operator_token),
        )
        assert first.status_code == 200

        second = client.post(
            f"/v1/recommendations/{rec.recommendation_id}/accept",
            headers=_auth(operator_token),
        )

    assert second.status_code == 409
    assert second.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)


async def test_reject_returns_200(app_ctx, operator_token):
    app, repo, _sink = app_ctx
    rec = await _seed(repo)

    with _client(app) as client:
        resp = client.post(
            f"/v1/recommendations/{rec.recommendation_id}/reject",
            headers=_auth(operator_token),
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


# ---------------------------------------------------------------------------
# auth: 401 (no/invalid token) and 403 (insufficient role)
# ---------------------------------------------------------------------------
async def test_missing_token_is_401(app_ctx):
    app, repo, _sink = app_ctx
    await _seed(repo)

    with _client(app) as client:
        resp = client.get("/v1/recommendations")

    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)


async def test_invalid_token_is_401(app_ctx):
    app, _repo, _sink = app_ctx
    with _client(app) as client:
        resp = client.get("/v1/recommendations", headers=_auth("not-a-real-jwt"))

    assert resp.status_code == 401


async def test_viewer_cannot_accept_is_403(app_ctx, viewer_token):
    """A viewer holds DATA_READ but not accept:recommendation -> 403 Forbidden."""

    app, repo, _sink = app_ctx
    rec = await _seed(repo)

    with _client(app) as client:
        resp = client.post(
            f"/v1/recommendations/{rec.recommendation_id}/accept",
            headers=_auth(viewer_token),
        )

    assert resp.status_code == 403
    assert resp.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)


async def test_viewer_can_still_read(app_ctx, viewer_token):
    app, repo, _sink = app_ctx
    await _seed(repo)

    with _client(app) as client:
        resp = client.get("/v1/recommendations", headers=_auth(viewer_token))

    assert resp.status_code == 200
