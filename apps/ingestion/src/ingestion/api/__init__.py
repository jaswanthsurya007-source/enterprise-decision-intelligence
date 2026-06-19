"""I2 — the ingestion REST + control API.

Three routers, wired onto the app factory (``ingestion.app.create_app``):

* :mod:`ingestion.api.routes_ingest`  — ``POST /v1/ingest/{sales,ops}`` (single or
  batch; batch returns a 207-style partial result), driving the **same** pipeline
  core (:func:`ingestion.pipeline.engine.ingest_record`) the simulator and batch
  loader use.
* :mod:`ingestion.api.routes_control` — ``POST /v1/control/simulator/{start,stop,
  inject}`` and ``POST /v1/control/seed``: the demo's remote control. These call a
  thin :class:`~ingestion.api.routes_control.SimulatorController` interface defined
  here and *implemented by I3* (the simulator/CLI layer), so this unit is buildable
  and unit-testable now with a stub controller.
* :mod:`ingestion.api.routes_health` — ``GET /v1/health`` (liveness) and
  ``/v1/health/ready`` (readiness: sink + idempotency guard started).

Auth uses the shared :mod:`edis_platform.authz` dependencies (dev JWT ->
:class:`~edis_contracts.security.SecurityContext`); every write is tenant-scoped by
the verified token (never the request body) and audited as ``DATA_WRITE``. Errors
are RFC 9457 ``application/problem+json`` via :mod:`edis_platform.errors`.
"""

from __future__ import annotations

from ingestion.api.routes_control import (
    SimulatorController,
    router as control_router,
)
from ingestion.api.routes_health import router as health_router
from ingestion.api.routes_ingest import router as ingest_router

__all__ = [
    "SimulatorController",
    "control_router",
    "health_router",
    "ingest_router",
    "include_routers",
]


def include_routers(app) -> None:
    """Mount the three I2 routers onto ``app`` (called from the app factory)."""

    app.include_router(health_router)
    app.include_router(ingest_router)
    app.include_router(control_router)
