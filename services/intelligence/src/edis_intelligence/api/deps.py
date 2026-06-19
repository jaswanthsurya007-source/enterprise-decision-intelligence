"""FastAPI dependencies for the L3 read API.

Two seams:

* :func:`get_repo` resolves the :class:`~edis_intelligence.store.repositories.IntelligenceRepo`
  off ``app.state`` (the app factory stashes it there — an in-memory fake in the bare
  app, the SQLAlchemy repo when a database is wired). When none is present it returns a
  ``404``-friendly ``None`` so the route degrades explicitly rather than 500-ing.
* :func:`get_principal` re-exports the platform's
  :func:`~edis_platform.authz.deps.get_security_context` so the tenant is always read
  from the verified JWT, never a request param. ``tenant_id`` therefore comes only from
  the token, matching the MVP's app-level tenant isolation.

FastAPI/Starlette are imported lazily where needed so importing this module needs no
running web app.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from edis_platform.authz.deps import get_security_context
from edis_platform.errors import NotFoundError
from starlette.requests import Request

if TYPE_CHECKING:
    from edis_contracts.security import SecurityContext

    from edis_intelligence.store.repositories import IntelligenceRepo


def get_repo(request: Request) -> "IntelligenceRepo":
    """Return the repo from ``app.state``; raise 404 when no store is wired.

    The bare app (no DB) still sets an in-memory repo, so this normally succeeds; the
    explicit guard keeps a misconfigured deployment from 500-ing on every read.
    """

    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise NotFoundError("Intelligence store is not configured.")
    return repo


async def get_principal(request: Request) -> "SecurityContext":
    """Resolve the verified principal (tenant + roles) from the bearer token."""

    return await get_security_context(request)
