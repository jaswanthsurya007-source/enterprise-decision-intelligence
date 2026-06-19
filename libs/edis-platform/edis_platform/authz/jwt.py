"""JWT decoding/minting -> :class:`SecurityContext`.

The dev-mode static token carries ``{tenant_id, sub, roles, scopes}`` and is
validated server-side with HS256. ``tenant_id`` and ``roles`` come ONLY from the
verified token -- never a request body. ``make_dev_token`` mints the static token
the dashboard uses in the MVP.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import jwt
from edis_contracts.security import SecurityContext

from edis_platform.errors import AuthError

if TYPE_CHECKING:
    from edis_platform.settings import Settings


def decode_token(token: str, settings: "Settings") -> SecurityContext:
    """Validate an HS256 JWT and map its claims to a :class:`SecurityContext`.

    Raises :class:`AuthError` on any invalid/expired token or a missing
    ``tenant_id`` claim.
    """

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("Invalid authentication token.") from exc

    tenant_id = claims.get("tenant_id")
    if not tenant_id:
        raise AuthError("Token is missing the required tenant_id claim.")

    return SecurityContext(
        tenant_id=str(tenant_id),
        user_id=str(claims.get("sub")),
        roles=list(claims.get("roles", []) or []),
        scopes=list(claims.get("scopes", []) or []),
        token_id=claims.get("jti"),
    )


def make_dev_token(
    tenant_id: str,
    user_id: str,
    roles: list[str],
    scopes: list[str],
    settings: "Settings",
    *,
    expires_in: timedelta = timedelta(days=7),
) -> str:
    """Mint the dev-mode static JWT used to exercise authz end-to-end."""

    now = datetime.now(tz=timezone.utc)
    payload = {
        "tenant_id": tenant_id,
        "sub": user_id,
        "roles": roles,
        "scopes": scopes,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_in).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
