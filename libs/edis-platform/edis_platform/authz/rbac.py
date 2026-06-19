"""Static, table-driven RBAC evaluated as a pure function.

``evaluate(ctx, action, resource)`` is a pure function of the verified
:class:`SecurityContext`, the requested action, and the :class:`ResourceRef` --
no DB, no I/O. Permissions are expressed as ``"<action>:<resource_type>"``
strings. ``admin`` is allowed everything; ``auditor`` reads audit/lineage data;
``operator`` can act on recommendations; ``analyst`` reads + queries the copilot;
``viewer`` is read-only. A ``"*"`` wildcard on either side matches any value.

Actions used across the platform map onto the :class:`AuditEvent` action enum
(``DATA_READ``, ``DATA_WRITE``, ``AI_DECISION``, ``AI_QUERY``, ``EXPORT``,
``RBAC_CHANGE``) plus lifecycle verbs (``accept``/``reject``) the gateway issues.
"""

from __future__ import annotations

from edis_contracts.security import ResourceRef, SecurityContext

#: role -> set of "action:resource_type" permissions. ``*`` is a wildcard.
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset(
        {
            "DATA_READ:kpi",
            "DATA_READ:metric",
            "DATA_READ:finding",
            "DATA_READ:forecast",
            "DATA_READ:recommendation",
        }
    ),
    "analyst": frozenset(
        {
            "DATA_READ:*",
            "AI_QUERY:copilot",
            "AI_QUERY:*",
        }
    ),
    "operator": frozenset(
        {
            "DATA_READ:*",
            "AI_QUERY:copilot",
            "AI_QUERY:*",
            "DATA_WRITE:recommendation",
            "accept:recommendation",
            "reject:recommendation",
            "DATA_WRITE:outcome",
        }
    ),
    "auditor": frozenset(
        {
            "DATA_READ:audit",
            "DATA_READ:lineage",
            "DATA_READ:decision",
            "DATA_READ:evidence",
            "EXPORT:audit",
        }
    ),
    "admin": frozenset({"*:*"}),
}


def _permission_matches(granted: str, requested: str) -> bool:
    """True if a granted ``action:resource_type`` covers the requested one."""

    g_action, _, g_resource = granted.partition(":")
    r_action, _, r_resource = requested.partition(":")
    action_ok = g_action == "*" or g_action == r_action
    resource_ok = g_resource == "*" or g_resource == r_resource
    return action_ok and resource_ok


def evaluate(ctx: SecurityContext, action: str, resource: ResourceRef) -> bool:
    """Return True iff any of the principal's roles grants ``action`` on ``resource``.

    Pure and side-effect free. ``admin`` short-circuits to allow.
    """

    requested = f"{action}:{resource.type}"
    for role in ctx.roles:
        if role == "admin":
            return True
        for granted in ROLE_PERMISSIONS.get(role, frozenset()):
            if _permission_matches(granted, requested):
                return True
    return False
