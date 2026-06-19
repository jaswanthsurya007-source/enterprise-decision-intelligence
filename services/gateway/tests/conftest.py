"""Shared fixtures for the gateway unit suite — no Docker, no broker, no keys.

Everything here runs against the in-memory fakes: an :class:`InMemoryGatewayRepo`
seeded with canonical contract models, the in-process bus for SSE, and a dev JWT
minted with the platform's :func:`make_dev_token`. The app is built with
:func:`create_app` and its ``app.state`` is overridden to point at the fakes, so
the real routes/deps/auth are exercised end-to-end with zero infrastructure.
"""

from __future__ import annotations

import pytest
from edis_platform.authz.jwt import make_dev_token
from edis_platform.settings import Settings
from gw_testkit import (  # re-exported below for ``from conftest import ...`` callers
    OTHER_TENANT,
    TENANT,
    make_finding,
    make_forecast,
    make_kpi,
    make_recommendation,
)

from edis_gateway.repository import InMemoryGatewayRepo

__all__ = [
    "OTHER_TENANT",
    "TENANT",
    "make_finding",
    "make_forecast",
    "make_kpi",
    "make_recommendation",
]


@pytest.fixture
def settings() -> Settings:
    """Plain in-proc settings (no infra). Distinct instance => isolated bus broker."""

    return Settings(sink_backend="inproc", jwt_secret="test-secret", database_url="")


@pytest.fixture
def token(settings: Settings) -> str:
    """A dev JWT for an analyst in TENANT (can read everything + query copilot)."""

    return make_dev_token(
        tenant_id=TENANT,
        user_id="u-analyst",
        roles=["analyst"],
        scopes=[],
        settings=settings,
    )


@pytest.fixture
def viewer_token(settings: Settings) -> str:
    """A dev JWT for a viewer in TENANT (read-only; no AI_QUERY)."""

    return make_dev_token(
        tenant_id=TENANT,
        user_id="u-viewer",
        roles=["viewer"],
        scopes=[],
        settings=settings,
    )


@pytest.fixture
def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def repo() -> InMemoryGatewayRepo:
    """An in-memory repo seeded for TENANT plus one cross-tenant row of each kind."""

    return InMemoryGatewayRepo(
        kpis=[make_kpi(), make_kpi(OTHER_TENANT)],
        anomalies=[make_finding(), make_finding(OTHER_TENANT)],
        recommendations=[
            make_recommendation(priority_rank=2, priority_score=0.40),
            make_recommendation(priority_rank=1, priority_score=0.93),
            make_recommendation(OTHER_TENANT, priority_rank=1),
        ],
        forecasts=[make_forecast(), make_forecast(OTHER_TENANT)],
    )


@pytest.fixture
def app(settings: Settings, repo: InMemoryGatewayRepo, monkeypatch):
    """Build the real gateway app, then point app.state at the in-memory fakes.

    The platform JWT dep reads ``get_settings()`` at request time, so we patch it
    to the test settings (same secret the token was minted with).
    """

    import edis_platform.authz.deps as authz_deps
    import edis_platform.settings as platform_settings_mod

    monkeypatch.setattr(platform_settings_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(authz_deps, "get_settings", lambda: settings)

    from edis_gateway.main import create_app

    application = create_app()
    application.state.platform_settings = settings
    application.state.repo = repo
    return application


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
