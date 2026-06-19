"""FastAPI dependencies for the gateway edge.

The gateway is the authoritative authorization boundary, so the tenant is read
ONLY from the verified JWT here:

* :func:`get_principal` re-exports the platform's
  :func:`~edis_platform.authz.deps.get_security_context` — the bearer token is
  validated into a :class:`SecurityContext` whose ``tenant_id`` scopes everything
  downstream. No route ever takes a tenant from a query/body.
* :func:`require_read` enforces a coarse ``DATA_READ`` gate via the platform RBAC
  matrix (every dashboard role can read; the gate exists so a token with no read
  role is rejected at the edge with RFC 9457 ``403``).
* :func:`get_repo` / :func:`get_source_factory` / :func:`get_gateway_settings`
  resolve the collaborators stashed on ``app.state`` by the app factory, so the
  routes stay infra-agnostic and unit-testable.

FastAPI/Starlette are imported lazily so this module imports with no web app.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from edis_contracts.security import ResourceRef, SecurityContext
from edis_platform.authz.deps import get_security_context
from edis_platform.authz.rbac import evaluate
from edis_platform.errors import ForbiddenError, NotFoundError
from starlette.requests import Request

if TYPE_CHECKING:
    from edis_gateway.config import GatewaySettings
    from edis_gateway.repository import GatewayRepo


async def get_principal(request: Request) -> SecurityContext:
    """Resolve the verified principal (tenant + roles) from the bearer token."""

    return await get_security_context(request)


def require_read(resource_type: str):
    """Dependency factory: require ``DATA_READ`` on ``resource_type`` via RBAC.

    Returns the :class:`SecurityContext` on success; raises RFC 9457 ``403`` when
    the principal's roles grant no read on the resource. The gateway is the
    authoritative gate — the React role guards are UX only.
    """

    async def _dep(request: Request) -> SecurityContext:
        ctx = await get_security_context(request)
        if not evaluate(ctx, "DATA_READ", ResourceRef(type=resource_type)):
            raise ForbiddenError(f"Requires DATA_READ on {resource_type}.")
        return ctx

    return _dep


def get_repo(request: Request) -> "GatewayRepo":
    """Return the read repo from ``app.state`` (404 if no store is wired)."""

    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise NotFoundError("Gateway store is not configured.")
    return repo


def get_gateway_settings(request: Request) -> "GatewaySettings":
    """Return the gateway settings stashed on ``app.state``."""

    return request.app.state.gateway_settings


def get_source_factory(request: Request):
    """Return the ``MessageSource`` factory stashed on ``app.state``.

    A zero-arg callable producing a fresh :class:`~edis_platform.bus.base.MessageSource`.
    Each SSE connection gets its own source/consumer-group so streams are isolated
    and a disconnect tears down only that subscription.
    """

    return request.app.state.source_factory


def get_copilot_proxy(request: Request):
    """Return the copilot SSE proxy stashed on ``app.state``."""

    proxy = getattr(request.app.state, "copilot_proxy", None)
    if proxy is None:
        raise NotFoundError("Copilot proxy is not configured.")
    return proxy
