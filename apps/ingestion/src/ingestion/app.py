"""FastAPI app factory for the ingestion service.

Wires the cross-cutting platform machinery (JSON logging, OTel bootstrap, RFC9457
error handlers) and manages the **lazy** lifecycle of the shared collaborators the
pipeline needs: the :class:`EventSink`, the
:class:`~ingestion.publish.publisher.IngestPublisher`, the idempotency guard, and
the outbox :class:`~ingestion.storage.raw_writer.RawWriter`.

Import-safe with **no infra**: building the app (``create_app()``) connects to
nothing — the sink/idempotency backends are only *started* in the lifespan
handler, and even then default to the in-proc / in-memory backends. The shared
collaborators are stashed on ``app.state`` so I2's routers (added later) can pull
them via FastAPI dependencies.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from edis_platform.bus.base import make_sink
from edis_platform.errors import install_exception_handlers
from edis_platform.logging import configure_logging, get_logger
from edis_platform.otel import init_telemetry, instrument_fastapi

from ingestion.config import get_ingestion_settings, get_settings
from ingestion.pipeline.idempotency import make_idempotency_store
from ingestion.publish.publisher import IngestPublisher

if TYPE_CHECKING:
    from fastapi import FastAPI

_log = get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: "FastAPI"):
    """Start the sink + idempotency guard on boot; stop them on shutdown."""

    state = app.state
    await state.sink.start()
    await state.idempotency.start()

    # Decide whether to persist the raw_events outbox. Persistence is on by
    # default, but is skipped when explicitly disabled (stateless ingestion) or
    # when the database is unreachable -- in which case we degrade to PUBLISH-ONLY
    # mode rather than failing every request (records still reach the bus; the
    # outbox is a replay safety-net, not the system of record).
    persisting = state.writer is not None and state.ingestion_settings.persist
    if state.writer is not None and not state.ingestion_settings.persist:
        _log.info("raw_events persistence disabled via settings -- publish-only mode")
        _disable_persistence(state)
    elif persisting and not await _db_reachable():
        _log.warning(
            "database unreachable -- ingestion running in PUBLISH-ONLY mode "
            "(raw_events outbox disabled; events are still published to the bus)"
        )
        _disable_persistence(state)
        persisting = False

    _log.info(
        "ingestion service started",
        extra={
            "sink_backend": state.platform_settings.sink_backend,
            "idempotency_backend": state.ingestion_settings.idempotency_backend,
            "persisting": persisting,
        },
    )
    try:
        yield
    finally:
        controller = getattr(state, "simulator_controller", None)
        shutdown = getattr(controller, "shutdown", None)
        if shutdown is not None:
            await shutdown()
        await state.idempotency.stop()
        await state.sink.stop()
        _log.info("ingestion service stopped")


def create_app() -> "FastAPI":
    """Build the ingestion FastAPI app. Connects to nothing at construction time."""

    from fastapi import FastAPI

    platform_settings = get_settings()
    ingestion_settings = get_ingestion_settings()

    configure_logging(ingestion_settings.service_name, platform_settings.log_level)
    init_telemetry(platform_settings)

    app = FastAPI(
        title="EDIS Ingestion (L1)",
        version="0.1.0",
        description="The edge of trust: validate → dedupe → land (outbox) → publish.",
        lifespan=_lifespan,
    )

    # Shared, process-wide collaborators (lazy; started in lifespan).
    sink = make_sink(platform_settings)
    app.state.platform_settings = platform_settings
    app.state.ingestion_settings = ingestion_settings
    app.state.sink = sink
    app.state.publisher = IngestPublisher(sink)
    app.state.idempotency = make_idempotency_store(ingestion_settings, platform_settings)
    app.state.writer = _maybe_make_writer()
    # I3: the real deterministic simulator controller, driving the same pipeline
    # core the REST ingest path uses. It satisfies the I2 SimulatorController
    # protocol, so the control routes (start/stop/inject/seed/status) need no change.
    from ingestion.sim_control import SimulatorController

    app.state.simulator_controller = SimulatorController(
        app.state.publisher,
        app.state.idempotency,
        writer=app.state.writer,
        settings=ingestion_settings,
    )

    install_exception_handlers(app)
    instrument_fastapi(app)

    # I2 routers: health (/v1/health[/ready]), ingest (/v1/ingest/{sales,ops}),
    # control (/v1/control/...). A minimal liveness probe keeps the bare app usable.
    from ingestion.api import include_routers

    include_routers(app)

    @app.get("/health/live", tags=["health"])
    async def _live() -> dict[str, str]:  # pragma: no cover - trivial
        return {"status": "ok"}

    return app


def _disable_persistence(state) -> None:
    """Switch the app to publish-only: drop the writer and tell the controller."""

    state.writer = None
    controller = getattr(state, "simulator_controller", None)
    if controller is not None and hasattr(controller, "set_writer"):
        controller.set_writer(None)


async def _db_reachable(timeout: float = 2.0) -> bool:
    """Best-effort database connectivity probe; never raises.

    Runs a short ``SELECT 1`` against the platform sessionmaker with a timeout so
    a refused or slow connection degrades quickly instead of hanging startup.
    """

    try:
        from sqlalchemy import text

        from edis_platform.db.session import get_sessionmaker

        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=timeout)
        return True
    except Exception:
        return False


def _maybe_make_writer():
    """Build the outbox writer from the lazy platform sessionmaker.

    Returns ``None`` if SQLAlchemy/session wiring is unavailable so a pure
    in-memory stream run (no Postgres) still works. The sessionmaker itself opens
    no connection until first use.
    """

    try:
        from edis_platform.db.session import get_sessionmaker

        from ingestion.storage.raw_writer import RawWriter

        return RawWriter(get_sessionmaker())
    except Exception:  # pragma: no cover - defensive: app must import without a DB
        return None
