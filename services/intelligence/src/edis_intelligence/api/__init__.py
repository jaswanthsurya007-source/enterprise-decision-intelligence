"""Read API for the intelligence (L3) service.

A small, tenant-scoped read surface over the persisted findings / forecasts so the
gateway and operators can pull L3 output without touching the bus:

* ``GET /v1/health``               — liveness.
* ``GET /v1/findings``             — tenant-scoped, paginated finding list.
* ``GET /v1/findings/{id}``        — one finding by id.
* ``GET /v1/forecasts``           — tenant-scoped, paginated forecast list.

The router reads the repo off ``app.state`` (set by the app factory) and the tenant
off the verified :class:`SecurityContext`, so tenant isolation comes from the token,
never a query param.
"""

from __future__ import annotations

from edis_intelligence.api.router import router

__all__ = ["router"]
