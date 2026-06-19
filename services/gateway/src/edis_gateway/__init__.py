"""EDIS API Gateway / BFF (W1) — the single frontend edge.

The gateway is the authoritative authorization boundary for the dashboard (L6):
it validates the dev JWT into a :class:`~edis_contracts.security.SecurityContext`,
scopes **every** request to the tenant carried by that verified token (never a
request body or query param), and exposes three concerns to the browser:

* tenant-scoped REST **snapshots** — ``/v1/kpis`` (L2 daily metric rollup),
  ``/v1/anomalies`` (L3 findings), ``/v1/recommendations`` (L4 recommendations,
  priority-sorted), ``/v1/forecasts`` (L3 forecasts) — behind a repo ``Protocol``
  with an in-memory fake so the routes are unit-testable with no infrastructure;
* live **SSE bridges** — one ``text/event-stream`` per concern at
  ``/v1/stream/{metrics,anomalies,recommendations}`` that subscribe the
  ``edis.metrics.points.v1`` / ``edis.findings.v1`` /
  ``edis.decisions.recommendations.v1`` topics via ``make_source`` and push each
  tenant-matching event to the browser, with periodic heartbeats;
* an SSE passthrough **proxy** to the copilot service ``POST /v1/copilot/chat``
  via ``httpx`` streaming, with the JWT + tenant enforced here at the edge.

Importing this package connects to nothing (no Postgres / broker / copilot), so
the service imports cleanly in CI without any infrastructure or API keys.
"""

from __future__ import annotations

__all__ = ["create_app"]


def create_app():
    """Lazily build and return the gateway FastAPI app (see :mod:`edis_gateway.main`)."""

    from edis_gateway.main import create_app as _create_app

    return _create_app()
