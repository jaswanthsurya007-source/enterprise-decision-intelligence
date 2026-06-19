"""FastAPI app factory for the integration (L2) service.

Wires the cross-cutting platform machinery (JSON logging, OTel bootstrap, RFC9457
error handlers) and stashes the shared, lazily-started collaborators on
``app.state`` (the :class:`EventSink` the outbox relay publishes through, and the
service settings). Building the app connects to **nothing** -- the sink defaults
to the in-proc backend and is only *started* in the lifespan handler -- so the
service imports cleanly in CI with no Postgres / Redpanda / Redis.

The consumers (stream/batch) and the outbox relay are separate units; this
factory provides the app + lifecycle they hang off, and a minimal liveness probe
keeps the bare app usable.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from edis_platform.bus.base import make_sink
from edis_platform.errors import install_exception_handlers
from edis_platform.logging import configure_logging, get_logger
from edis_platform.otel import init_telemetry, instrument_fastapi

from edis_integration.config import get_integration_settings, get_settings

if TYPE_CHECKING:
    from fastapi import FastAPI

_log = get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: "FastAPI"):
    """Start the sink on boot; wire persistence collaborators; stop on shutdown.

    Persistence wiring is lazy and best-effort: when ``persist`` is true we build
    the SQLAlchemy repo + outbox reader + quarantine repo (no connection is opened
    until first use), and a batch loader for the reprocess route. When ``persist``
    is false (the in-proc / no-DB mode) the in-memory repo + outbox reader are
    wired instead, so every ops/admin route still works.
    """

    state = app.state
    await state.sink.start()
    _wire_persistence(app)
    _log.info(
        "integration service started",
        extra={
            "sink_backend": state.platform_settings.sink_backend,
            "metric_bucket": state.integration_settings.metric_bucket,
            "persist": state.integration_settings.persist,
        },
    )
    try:
        yield
    finally:
        await state.sink.stop()
        _log.info("integration service stopped")


def _wire_persistence(app: "FastAPI") -> None:
    """Attach repo / outbox_reader / quarantine_repo / batch_loader to app.state."""

    state = app.state
    if state.integration_settings.persist:
        from edis_integration.consumers.batch_loader import BatchLoader
        from edis_integration.outbox.outbox_repo import SqlAlchemyOutboxRepo
        from edis_integration.persistence.db import get_sessionmaker
        from edis_integration.persistence.repositories import (
            SqlAlchemyIntegrationRepo,
            SqlAlchemyQuarantineRepo,
        )

        sm = get_sessionmaker()
        repo = SqlAlchemyIntegrationRepo(sm)
        outbox_reader = SqlAlchemyOutboxRepo(sm)
        quarantine_repo = SqlAlchemyQuarantineRepo(sm)
    else:
        from edis_integration.consumers.batch_loader import BatchLoader
        from edis_integration.outbox.outbox_repo import InMemoryOutboxRepo
        from edis_integration.pipeline.engine import InMemoryIntegrationRepo

        repo = InMemoryIntegrationRepo()
        outbox_reader = InMemoryOutboxRepo(repo)
        quarantine_repo = None

    settings = state.integration_settings
    state.repo = repo
    state.outbox_reader = outbox_reader
    state.quarantine_repo = quarantine_repo
    state.consumer = None
    state.batch_loader = BatchLoader(
        repo,
        state.sink,
        outbox_reader,
        metric_bucket=settings.metric_bucket,
        dq_min_score=settings.dq_min_score,
        max_records=settings.batch_max_records,
    )


def create_app() -> "FastAPI":
    """Build the integration FastAPI app. Connects to nothing at construction time."""

    from fastapi import FastAPI

    platform_settings = get_settings()
    integration_settings = get_integration_settings()

    configure_logging(integration_settings.service_name, platform_settings.log_level)
    init_telemetry(platform_settings)

    app = FastAPI(
        title="EDIS Integration (L2)",
        version="0.1.0",
        description=(
            "System-of-record gatekeeper: decode -> validate -> map -> clean -> "
            "coerce -> DQ -> deterministic upsert -> derive metrics -> outbox-publish."
        ),
        lifespan=_lifespan,
    )

    # Shared, process-wide collaborators (lazy; started in lifespan).
    sink = make_sink(platform_settings)
    app.state.platform_settings = platform_settings
    app.state.integration_settings = integration_settings
    app.state.sink = sink

    install_exception_handlers(app)
    instrument_fastapi(app)

    # Ops/admin surface: /v1/health, /v1/integration/{lag,quarantine,reprocess}.
    from edis_integration.api.router import router as integration_router

    app.include_router(integration_router)

    @app.get("/health/live", tags=["health"])
    async def _live() -> dict[str, str]:  # pragma: no cover - trivial
        return {"status": "ok"}

    return app
