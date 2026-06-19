"""FastAPI dependencies shared across the I2 routers.

The app factory (:func:`ingestion.app.create_app`) stashes the process-wide
collaborators on ``app.state`` (sink, :class:`IngestPublisher`, idempotency guard,
outbox :class:`RawWriter`, settings) so they are created once, started in the
lifespan, and never reconnect per request. These accessors read them off
``request.app.state`` for the route handlers.

Auth is re-exported from :mod:`edis_platform.authz.deps`: ``get_security_context``
verifies the bearer JWT into a :class:`~edis_contracts.security.SecurityContext`
(tenant + roles come ONLY from the token), and ``require_role`` gates control-plane
operations. ``tenant_id`` for a write is therefore always the verified token's
tenant — never the request body.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from edis_platform.authz.deps import (  # re-export for routers
    get_security_context,
    require_role,
)
from starlette.requests import Request

if TYPE_CHECKING:
    from ingestion.config import IngestionSettings
    from ingestion.pipeline.idempotency import IdempotencyStore
    from ingestion.publish.publisher import IngestPublisher
    from ingestion.storage.raw_writer import RawWriter

__all__ = [
    "get_security_context",
    "require_role",
    "get_publisher",
    "get_idempotency",
    "get_writer",
    "get_ingestion_settings_dep",
    "get_simulator_controller",
]


def get_publisher(request: Request) -> "IngestPublisher":
    """The shared :class:`IngestPublisher` (topics + keys + audit)."""

    return request.app.state.publisher


def get_idempotency(request: Request) -> "IdempotencyStore":
    """The shared idempotency guard (in-memory in tests, Redis ``SETNX`` in prod)."""

    return request.app.state.idempotency


def get_writer(request: Request) -> "RawWriter | None":
    """The outbox :class:`RawWriter`, or ``None`` when running with no database."""

    return getattr(request.app.state, "writer", None)


def get_ingestion_settings_dep(request: Request) -> "IngestionSettings":
    """The process :class:`IngestionSettings` (publish-after-land flag, defaults)."""

    return request.app.state.ingestion_settings


def get_simulator_controller(request: Request):
    """The :class:`~ingestion.api.routes_control.SimulatorController` (I3-provided).

    The app factory attaches a controller to ``app.state.simulator_controller``;
    until I3 lands its real implementation, a no-op stub is attached so the
    control routes are wired and testable. Returns ``None`` only if nothing was
    attached at all (defensive).
    """

    return getattr(request.app.state, "simulator_controller", None)
