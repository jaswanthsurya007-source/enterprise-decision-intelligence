"""FastAPI dependencies for authentication and authorization.

``get_security_context`` parses the ``Authorization: Bearer <jwt>`` header into a
verified :class:`SecurityContext`. ``require_role`` / ``require_scope`` are
dependency factories that enforce coarse role/scope gates, raising
:class:`ForbiddenError` (RFC 9457) when the principal lacks them. Fine-grained,
resource-aware checks use :func:`edis_platform.authz.rbac.evaluate` directly.

FastAPI/Starlette are imported lazily inside the dependencies so this module is
importable without a running web app.
"""

from __future__ import annotations

from edis_contracts.security import SecurityContext
from starlette.requests import Request

from edis_platform.authz.jwt import decode_token
from edis_platform.errors import AuthError, ForbiddenError
from edis_platform.settings import get_settings


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise AuthError("Missing Authorization header.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthError("Authorization header must be 'Bearer <token>'.")
    return token.strip()


async def get_security_context(request: Request) -> SecurityContext:
    """Resolve the verified principal from the request's bearer token."""

    token = _extract_bearer(request.headers.get("Authorization"))
    return decode_token(token, get_settings())


def require_role(*roles: str):
    """Dependency factory: require the principal to hold at least one role."""

    required = set(roles)

    async def _dep(request: Request) -> SecurityContext:
        ctx = await get_security_context(request)
        if "admin" in ctx.roles:
            return ctx
        if required.isdisjoint(ctx.roles):
            raise ForbiddenError(f"Requires one of roles: {', '.join(sorted(required))}.")
        return ctx

    return _dep


def require_scope(*scopes: str):
    """Dependency factory: require the principal to hold all listed scopes."""

    required = set(scopes)

    async def _dep(request: Request) -> SecurityContext:
        ctx = await get_security_context(request)
        missing = required.difference(ctx.scopes)
        if missing:
            raise ForbiddenError(f"Requires scope(s): {', '.join(sorted(missing))}.")
        return ctx

    return _dep
