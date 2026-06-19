"""FastAPI app factory for the intelligence (L3) service.

Wires the cross-cutting platform machinery (JSON logging, OTel bootstrap, RFC9457
error handlers) and stashes the shared collaborators on ``app.state``. Building the
app connects to **nothing** — no Postgres / Redpanda / Redis, and no Anthropic /
Voyage API key is required — so the service imports cleanly in CI.

The detectors + scoring core (X1) are pure and need no app at all. This factory (X3)
hangs the IO layer off the app: the publisher (``make_sink``), the read-API repo, the
grounded narrator (lazy Claude client; template fallback when no key), and the Voyage
embedder (stub fallback when no key). The store + repo default to an in-memory fake so
the bare app boots with no database; a SQLAlchemy repo is selected automatically once a
non-default ``database_url`` is configured.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from edis_platform.bus.base import make_sink
from edis_platform.errors import install_exception_handlers
from edis_platform.logging import configure_logging, get_logger
from edis_platform.otel import init_telemetry, instrument_fastapi

from edis_intelligence.api.router import router as intelligence_router
from edis_intelligence.config import get_intelligence_settings, get_settings
from edis_intelligence.grounding.claude_client import make_narration_client
from edis_intelligence.grounding.embeddings import make_embedder
from edis_intelligence.rca.narrator import make_narrator
from edis_intelligence.store.publisher import IntelligencePublisher
from edis_intelligence.store.repositories import make_repo

if TYPE_CHECKING:
    from fastapi import FastAPI

_log = get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: "FastAPI"):
    """Start the sink on boot; stop on shutdown.

    The sink is the :class:`EventSink` through which findings/forecasts/lineage are
    published (X3). Building it opens no connection; it is only *started* here, and
    defaults to the in-proc backend so the service boots with no broker.
    """

    state = app.state
    await state.sink.start()
    _log.info(
        "intelligence service started",
        extra={
            "sink_backend": state.platform_settings.sink_backend,
            "z_threshold": state.intelligence_settings.z_threshold,
            "stl_period": state.intelligence_settings.stl_period,
            "baseline_days": state.intelligence_settings.baseline_days,
            "narration_enabled": state.narration_client is not None,
            "embedding_model": state.embedder.model,
        },
    )
    try:
        yield
    finally:
        await state.sink.stop()
        if state.narration_client is not None:
            await state.narration_client.aclose()
        _log.info("intelligence service stopped")


def create_app() -> "FastAPI":
    """Build the intelligence FastAPI app. Connects to nothing at construction time."""

    from fastapi import FastAPI

    platform_settings = get_settings()
    intelligence_settings = get_intelligence_settings()

    configure_logging(intelligence_settings.service_name, platform_settings.log_level)
    init_telemetry(platform_settings)

    app = FastAPI(
        title="EDIS Intelligence (L3)",
        version="0.1.0",
        description=(
            "Detect (robust z-score / STL level-shift) -> score -> correlate "
            "(lag-aware RCA) -> bundle evidence -> grounded narrate -> forecast band "
            "-> persist Finding + EvidenceBundle + Forecast -> publish."
        ),
        lifespan=_lifespan,
    )

    # Shared, process-wide collaborators (lazy; started in lifespan).
    sink = make_sink(platform_settings)

    # Read-API repo: in-memory fake unless a real (non-default) database is configured,
    # in which case the SQLAlchemy repo is selected over the platform sessionmaker.
    sessionmaker = _maybe_sessionmaker(platform_settings)
    repo = make_repo(platform_settings, sessionmaker)

    # Grounded narrator: a lazy Claude client (None when no key -> template path) and
    # the Voyage embedder (stub fallback when no key). Both degrade safely.
    narration_client = make_narration_client(platform_settings)
    narrator = make_narrator(narration_client, rel_tol=intelligence_settings.grounding_rel_tol)
    embedder = make_embedder(platform_settings, dim=intelligence_settings.embedding_dim)
    publisher = IntelligencePublisher(sink)

    app.state.platform_settings = platform_settings
    app.state.intelligence_settings = intelligence_settings
    app.state.sink = sink
    app.state.repo = repo
    app.state.narration_client = narration_client
    app.state.narrator = narrator
    app.state.embedder = embedder
    app.state.publisher = publisher

    install_exception_handlers(app)
    instrument_fastapi(app)
    app.include_router(intelligence_router)

    @app.get("/health/live", tags=["health"])
    async def _live() -> dict[str, str]:  # pragma: no cover - trivial
        return {"status": "ok"}

    return app


def _maybe_sessionmaker(platform_settings):
    """Build a SQLAlchemy sessionmaker iff a non-default database is configured.

    Connections stay lazy (the engine opens nothing until first use), so this never
    touches a live database at construction time. Returns ``None`` for the default
    localhost URL so the bare app uses the in-memory repo and boots with no Postgres.
    """

    default_url = "postgresql+asyncpg://edis:edis@localhost:5432/edis"
    if not platform_settings.database_url or platform_settings.database_url == default_url:
        return None
    from edis_platform.db.session import get_sessionmaker

    return get_sessionmaker()
