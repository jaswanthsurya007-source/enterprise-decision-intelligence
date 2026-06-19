"""RBAC admin API (``GET/POST /v1/rbac``) -- admin only.

``GET /v1/rbac`` returns the role registry + the persisted permission matrix
(the projection of :data:`edis_platform.authz.rbac.ROLE_PERMISSIONS`).
``POST /v1/rbac/permissions`` grants ``action:resource_type`` to a role
(idempotent) and emits an ``RBAC_CHANGE`` :class:`AuditEvent` through the bus so
the change itself is auditable. Both require the ``admin`` role.
"""

from __future__ import annotations

from typing import Annotated

from edis_contracts.security import ResourceRef, SecurityContext
from edis_gov_sdk.audit import emit_audit
from edis_platform.authz.rbac import ROLE_PERMISSIONS, evaluate
from edis_platform.errors import ForbiddenError
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.deps import get_rbac_repo, get_sink, require_role
from app.repo import RbacRepository

router = APIRouter(prefix="/v1/rbac", tags=["rbac"])

_require_admin = require_role("admin")


class RoleOut(BaseModel):
    name: str
    description: str | None = None


class PermissionOut(BaseModel):
    role: str
    action: str
    resource_type: str


class RbacView(BaseModel):
    """The full RBAC control-plane: roles + persisted permission matrix."""

    roles: list[RoleOut]
    permissions: list[PermissionOut]
    #: The static, code-defined matrix (source of truth for runtime evaluate()).
    static_matrix: dict[str, list[str]]


class PermissionGrant(BaseModel):
    role: str = Field(..., description="Role to grant the permission to.")
    action: str = Field(..., description='Action, e.g. "DATA_READ".')
    resource_type: str = Field(..., description='Resource type, e.g. "audit".')


@router.get("", response_model=RbacView)
async def get_rbac(
    ctx: Annotated[SecurityContext, Depends(_require_admin)],
    repo: Annotated[RbacRepository, Depends(get_rbac_repo)],
) -> RbacView:
    """Return roles + the persisted permission matrix + the static code matrix."""

    if not evaluate(ctx, "DATA_READ", ResourceRef(type="rbac")):
        raise ForbiddenError("Not permitted to read RBAC configuration.")

    roles = await repo.list_roles()
    perms = await repo.list_permissions()
    return RbacView(
        roles=[RoleOut(name=r.name, description=r.description) for r in roles],
        permissions=[
            PermissionOut(role=p.role, action=p.action, resource_type=p.resource_type)
            for p in perms
        ],
        static_matrix={role: sorted(perms) for role, perms in ROLE_PERMISSIONS.items()},
    )


@router.post("/permissions", status_code=status.HTTP_201_CREATED)
async def grant_permission(
    grant: PermissionGrant,
    ctx: Annotated[SecurityContext, Depends(_require_admin)],
    repo: Annotated[RbacRepository, Depends(get_rbac_repo)],
    sink=Depends(get_sink),
) -> dict:
    """Grant ``action:resource_type`` to ``role`` (idempotent); audit the change."""

    if not evaluate(ctx, "RBAC_CHANGE", ResourceRef(type="rbac")):
        raise ForbiddenError("Not permitted to change RBAC configuration.")

    await repo.ensure_role(grant.role)
    created = await repo.upsert_permission(grant.role, grant.action, grant.resource_type)
    await repo.commit()

    if sink is not None:
        await emit_audit(
            sink,
            ctx,
            action="RBAC_CHANGE",
            resource={
                "type": "rbac",
                "id": grant.role,
                "permission": f"{grant.action}:{grant.resource_type}",
            },
            outcome="ALLOW",
            reason="granted" if created else "already-granted",
        )

    return {
        "role": grant.role,
        "permission": f"{grant.action}:{grant.resource_type}",
        "created": created,
    }
