"""FastAPI app factory for the API Gateway / BFF (W1) — the single frontend edge.

Wires the cross-cutting platform machinery (JSON logging, OTel bootstrap, RFC9457
error handlers) and stashes the edge collaborators on ``app.state``. Building the
app connects to **nothing** — no Postgres, no broker, no copilot, no API key — so
the service imports cleanly in CI and every REST/SSE route is unit-testable
against the in-memory fakes.

What hangs off the app:

* ``state.repo`` — the :class:`GatewayRepo` read port: an in-memory fake unless a
  non-default ``database_url`` is configured, in which case the SQLAlchemy reader
  is selected (lazy; opens no connection at construction).
* ``state.source_factory`` — a zero-arg factory producing a fresh
  :class:`MessageSource` per SSE connection (so each stream gets its own
  consumer-group). Defaults to the in-process bus so SSE works with no broker.
* ``state.copilot_proxy`` — the :class:`CopilotProxy` (shared ``httpx`` client),
  started/stopped in the lifespan.

The lifespan starts/stops only the copilot proxy's HTTP client; SSE sources are
started/stopped per connection by the bridge.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from edis_platform.bus.base import make_source
from edis_platform.errors import install_exception_handlers
from edis_platform.logging import configure_logging, get_logger
from edis_platform.otel import init_telemetry, instrument_fastapi

from edis_gateway.config import get_gateway_settings, get_settings
from edis_gateway.deps import get_principal
from edis_gateway.proxy.copilot import CopilotProxy
from edis_gateway.proxy.copilot import router as copilot_router
from edis_gateway.repository import make_repo
from edis_gateway.rest import rest_router
from edis_gateway.sse.stream import router as sse_router

if TYPE_CHECKING:
    from fastapi import FastAPI

_log = get_logger(__name__)

_DEFAULT_DB_URL = "postgresql+asyncpg://edis:edis@localhost:5432/edis"


@asynccontextmanager
async def _lifespan(app: "FastAPI"):
    """Start the copilot proxy HTTP client on boot; close it on shutdown.

    SSE message sources are per-connection (started/stopped by the bridge), so the
    lifespan only owns the long-lived copilot ``httpx`` client.
    """

    state = app.state
    await state.copilot_proxy.start()
    _log.info(
        "gateway started",
        extra={
            "sink_backend": state.platform_settings.sink_backend,
            "repo": type(state.repo).__name__,
            "copilot_base_url": state.gateway_settings.copilot_base_url,
            "sse_heartbeat_seconds": state.gateway_settings.sse_heartbeat_seconds,
        },
    )
    try:
        yield
    finally:
        await state.copilot_proxy.stop()
        _log.info("gateway stopped")


def create_app() -> "FastAPI":
    """Build the gateway FastAPI app. Connects to nothing at construction time."""

    from fastapi import Depends, FastAPI

    platform_settings = get_settings()
    gateway_settings = get_gateway_settings()

    configure_logging(gateway_settings.service_name, platform_settings.log_level)
    init_telemetry(platform_settings)

    app = FastAPI(
        title="EDIS API Gateway / BFF (W1)",
        version="0.1.0",
        description=(
            "The single frontend edge: dev-JWT auth + per-tenant scoping, REST "
            "snapshots (/v1/kpis, /v1/anomalies, /v1/recommendations, /v1/forecasts), "
            "Kafka->browser SSE bridges (/v1/stream/{metrics,anomalies,recommendations}), "
            "and an SSE passthrough proxy to the L5 copilot. Authoritative authz boundary."
        ),
        lifespan=_lifespan,
    )

    # --- read repo: in-memory fake unless a real database is configured ---
    sessionmaker = _maybe_sessionmaker(platform_settings)
    repo = make_repo(platform_settings, sessionmaker)

    # --- SSE source factory: a fresh MessageSource per connection ---
    def source_factory():
        return make_source(platform_settings)

    # --- copilot SSE proxy (httpx client started in lifespan) ---
    copilot_proxy = CopilotProxy(gateway_settings)

    app.state.platform_settings = platform_settings
    app.state.gateway_settings = gateway_settings
    app.state.repo = repo
    app.state.source_factory = source_factory
    app.state.copilot_proxy = copilot_proxy

    install_exception_handlers(app)
    instrument_fastapi(app)
    app.include_router(rest_router)
    app.include_router(sse_router)
    app.include_router(copilot_router)

    @app.get("/v1/health", tags=["health"], summary="Liveness probe")
    async def _health() -> dict[str, str]:  # pragma: no cover - trivial
        """Always-ok liveness signal (unauthenticated; probes carry no JWT)."""

        return {"status": "ok", "service": gateway_settings.service_name}

    @app.get("/v1/whoami", tags=["health"], summary="Echo the verified principal")
    async def _whoami(principal=Depends(get_principal)) -> dict:  # pragma: no cover
        """Return the JWT-derived tenant/roles (handy for verifying edge auth)."""

        return {
            "tenant_id": principal.tenant_id,
            "user_id": principal.user_id,
            "roles": principal.roles,
            "scopes": principal.scopes,
        }

    return app


def _maybe_sessionmaker(platform_settings):
    """Build a SQLAlchemy sessionmaker iff a non-default database is configured.

    Connections stay lazy (the engine opens nothing until first use), so this never
    touches a live database at construction time. Returns ``None`` for the default
    localhost URL so the bare app uses the in-memory repo and boots with no Postgres.
    """

    if not platform_settings.database_url or platform_settings.database_url == _DEFAULT_DB_URL:
        return None
    from edis_platform.db.session import get_sessionmaker

    return get_sessionmaker()


# A module-level app for ``uvicorn edis_gateway.main:app``.
app = create_app()
