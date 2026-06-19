"""Authentication and authorization: JWT -> SecurityContext, static RBAC, deps."""

from __future__ import annotations

from edis_platform.authz.deps import (
    get_security_context,
    require_role,
    require_scope,
)
from edis_platform.authz.jwt import decode_token, make_dev_token
from edis_platform.authz.rbac import ROLE_PERMISSIONS, evaluate

__all__ = [
    "decode_token",
    "make_dev_token",
    "ROLE_PERMISSIONS",
    "evaluate",
    "get_security_context",
    "require_role",
    "require_scope",
]
