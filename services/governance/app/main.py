"""Governance service FastAPI application (L7).

Wires the platform cross-cutting concerns (JSON logging, OTel bootstrap, RFC 9457
exception handlers), mounts the audit/explain/lineage/rbac routers, and -- at
startup -- launches the audit and lineage consumers as background tasks plus a
process-wide :class:`EventSink` (used by the RBAC admin route to emit
``RBAC_CHANGE`` audits).

**Importable without a live DB or broker:** ``create_app()`` builds no engine and
opens no connection; the consumers and sink connect lazily inside their
``start()`` during the lifespan, and any failure to start them is logged but does
not prevent the API from serving (governance reads must stay available even if the
bus is down). This is what lets CI ``import app.main`` with no infrastructure.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from edis_platform.bus.base import make_sink
from edis_platform.errors import install_exception_handlers
from edis_platform.logging import configure_logging, get_logger
from edis_platform.otel import init_telemetry, instrument_fastapi
from edis_platform.settings import Settings, get_settings
from fastapi import FastAPI

from app.api import audit as audit_api
from app.api import explain as explain_api
from app.api import lineage as lineage_api
from app.api import rbac as rbac_api
from app.consumers.audit_consumer import run_audit_consumer
from app.consumers.lineage_consumer import run_lineage_consumer

logger = get_logger("edis.governance")


async def _start_background(app: FastAPI, settings: Settings) -> None:
    """Start the sink + consumer tasks; tolerate broker-down (log, don't crash)."""

    tasks: list[asyncio.Task] = []
    try:
        sink = make_sink(settings)
        await sink.start()
        app.state.sink = sink
    except Exception:  # noqa: BLE001 - bus may be down; API still serves
        logger.exception("failed to start event sink; RBAC_CHANGE audits disabled")
        app.state.sink = None

    try:
        tasks.append(asyncio.create_task(run_audit_consumer(settings)))
        tasks.append(asyncio.create_task(run_lineage_consumer(settings)))
        logger.info("governance consumers launched")
    except Exception:  # noqa: BLE001
        logger.exception("failed to launch governance consumers")

    app.state.consumer_tasks = tasks


async def _stop_background(app: FastAPI) -> None:
    """Cancel consumer tasks and stop the sink on shutdown."""

    for task in getattr(app.state, "consumer_tasks", []):
        task.cancel()
    for task in getattr(app.state, "consumer_tasks", []):
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    sink = getattr(app.state, "sink", None)
    if sink is not None:
        try:
            await sink.stop()
        except Exception:  # noqa: BLE001
            logger.exception("error stopping event sink")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """App lifespan: launch consumers/sink at startup, tear them down at shutdown."""

    settings: Settings = app.state.settings
    await _start_background(app, settings)
    try:
        yield
    finally:
        await _stop_background(app)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the governance FastAPI app (no DB/broker connection at build time)."""

    settings = settings or get_settings()
    configure_logging(settings.service_name, settings.log_level)
    init_telemetry(settings)

    app = FastAPI(
        title="EDIS Governance",
        version="0.1.0",
        description=(
            "L7 governance spine: append-only audit log, lineage graph, "
            "explainability store, and static-RBAC admin."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings

    install_exception_handlers(app)
    instrument_fastapi(app)

    app.include_router(audit_api.router)
    app.include_router(explain_api.router)
    app.include_router(lineage_api.router)
    app.include_router(rbac_api.router)

    @app.get("/health", tags=["health"])
    async def health() -> dict:
        """Liveness probe -- does not touch the DB or broker."""

        return {"status": "ok", "service": settings.service_name}

    return app


app = create_app()
