"""Pure-function RBAC matrix tests -- no Docker, no I/O.

Exercises :func:`edis_platform.authz.rbac.evaluate` -- the static, table-driven
authorization function the governance API (and every other service) calls -- as a
pure function of ``(SecurityContext, action, ResourceRef)``. The expected
allow/deny outcomes below are written out explicitly (not derived from
``ROLE_PERMISSIONS``) so this file is an independent oracle: if someone changes
the permission table, these assertions catch the behavioral drift rather than
silently moving with it.

Roles under test: viewer / analyst / operator / auditor / admin.
"""

from __future__ import annotations

import pytest

from edis_contracts.security import ResourceRef, SecurityContext
from edis_platform.authz.rbac import ROLE_PERMISSIONS, evaluate


def _ctx(*roles: str, tenant_id: str = "tenant-a") -> SecurityContext:
    return SecurityContext(tenant_id=tenant_id, user_id="u", roles=list(roles))


# (role, action, resource_type, expected_allow) -- the authoritative matrix.
MATRIX: list[tuple[str, str, str, bool]] = [
    # --- viewer: read-only on the dashboard-facing resources ---
    ("viewer", "DATA_READ", "kpi", True),
    ("viewer", "DATA_READ", "metric", True),
    ("viewer", "DATA_READ", "finding", True),
    ("viewer", "DATA_READ", "forecast", True),
    ("viewer", "DATA_READ", "recommendation", True),
    ("viewer", "DATA_READ", "audit", False),  # no blanket DATA_READ:* for viewer
    ("viewer", "DATA_WRITE", "recommendation", False),
    ("viewer", "AI_QUERY", "copilot", False),
    ("viewer", "RBAC_CHANGE", "rbac", False),
    ("viewer", "EXPORT", "audit", False),
    # --- analyst: reads anything + queries the copilot, but cannot write ---
    ("analyst", "DATA_READ", "metric", True),
    ("analyst", "DATA_READ", "audit", True),  # DATA_READ:* wildcard
    ("analyst", "DATA_READ", "anything", True),
    ("analyst", "AI_QUERY", "copilot", True),
    ("analyst", "AI_QUERY", "agent", True),  # AI_QUERY:* wildcard
    ("analyst", "DATA_WRITE", "recommendation", False),
    ("analyst", "accept", "recommendation", False),
    ("analyst", "RBAC_CHANGE", "rbac", False),
    ("analyst", "EXPORT", "audit", False),
    # --- operator: analyst powers + acts on recommendations/outcomes ---
    ("operator", "DATA_READ", "metric", True),
    ("operator", "AI_QUERY", "copilot", True),
    ("operator", "DATA_WRITE", "recommendation", True),
    ("operator", "accept", "recommendation", True),
    ("operator", "reject", "recommendation", True),
    ("operator", "DATA_WRITE", "outcome", True),
    ("operator", "DATA_WRITE", "audit", False),  # not granted DATA_WRITE:audit
    ("operator", "RBAC_CHANGE", "rbac", False),
    ("operator", "EXPORT", "audit", False),
    # --- auditor: reads the governance store + exports audit; nothing else ---
    ("auditor", "DATA_READ", "audit", True),
    ("auditor", "DATA_READ", "lineage", True),
    ("auditor", "DATA_READ", "decision", True),
    ("auditor", "DATA_READ", "evidence", True),
    ("auditor", "EXPORT", "audit", True),
    ("auditor", "DATA_READ", "metric", False),  # auditor is NOT a general reader
    ("auditor", "DATA_READ", "kpi", False),
    ("auditor", "AI_QUERY", "copilot", False),
    ("auditor", "DATA_WRITE", "recommendation", False),
    ("auditor", "RBAC_CHANGE", "rbac", False),
    # --- admin: the "*:*" wildcard allows everything ---
    ("admin", "DATA_READ", "audit", True),
    ("admin", "DATA_WRITE", "recommendation", True),
    ("admin", "RBAC_CHANGE", "rbac", True),
    ("admin", "EXPORT", "audit", True),
    ("admin", "AI_DECISION", "finding", True),
    ("admin", "anything", "whatever", True),
]


@pytest.mark.parametrize(
    "role,action,resource_type,expected",
    MATRIX,
    ids=[f"{r}:{a}:{rt}:{'allow' if e else 'deny'}" for r, a, rt, e in MATRIX],
)
def test_rbac_matrix(role: str, action: str, resource_type: str, expected: bool) -> None:
    """Each (role, action, resource) maps to the documented allow/deny verdict."""

    ctx = _ctx(role)
    assert evaluate(ctx, action, ResourceRef(type=resource_type)) is expected


def test_no_roles_denies_everything() -> None:
    """A principal with no roles is denied every action (default-deny)."""

    ctx = _ctx()  # zero roles
    assert evaluate(ctx, "DATA_READ", ResourceRef(type="metric")) is False
    assert evaluate(ctx, "AI_QUERY", ResourceRef(type="copilot")) is False
    assert evaluate(ctx, "RBAC_CHANGE", ResourceRef(type="rbac")) is False


def test_unknown_role_is_ignored() -> None:
    """An unrecognized role grants nothing (no entry in the permission table)."""

    ctx = _ctx("superuser")  # not a real role
    assert evaluate(ctx, "DATA_READ", ResourceRef(type="metric")) is False


def test_multiple_roles_union_their_permissions() -> None:
    """Holding several roles grants the union of their permissions."""

    # viewer (reads kpi/metric/...) + auditor (reads audit/lineage) together can
    # read both sets, but still cannot write.
    ctx = _ctx("viewer", "auditor")
    assert evaluate(ctx, "DATA_READ", ResourceRef(type="metric")) is True  # via viewer
    assert evaluate(ctx, "DATA_READ", ResourceRef(type="audit")) is True  # via auditor
    assert evaluate(ctx, "EXPORT", ResourceRef(type="audit")) is True  # via auditor
    assert evaluate(ctx, "DATA_WRITE", ResourceRef(type="recommendation")) is False


def test_admin_role_short_circuits_even_with_other_roles() -> None:
    """If any held role is admin, everything is allowed regardless of order."""

    assert evaluate(_ctx("viewer", "admin"), "RBAC_CHANGE", ResourceRef(type="rbac")) is True
    assert evaluate(_ctx("admin", "viewer"), "DATA_WRITE", ResourceRef(type="x")) is True


def test_resource_id_and_columns_do_not_affect_type_match() -> None:
    """RBAC keys on ``action:resource_type``; a specific id/columns is irrelevant."""

    ctx = _ctx("viewer")
    bare = ResourceRef(type="metric")
    specific = ResourceRef(type="metric", id="revenue", columns=["value"])
    assert evaluate(ctx, "DATA_READ", bare) == evaluate(ctx, "DATA_READ", specific) is True


def test_evaluate_is_pure_and_non_mutating() -> None:
    """``evaluate`` does not mutate its inputs (no side effects)."""

    ctx = _ctx("analyst")
    roles_before = list(ctx.roles)
    res = ResourceRef(type="metric")
    evaluate(ctx, "DATA_READ", res)
    assert ctx.roles == roles_before
    assert res.type == "metric"


def test_static_matrix_covers_the_five_named_roles() -> None:
    """The shipped permission table defines exactly the five documented roles."""

    assert set(ROLE_PERMISSIONS) == {"viewer", "analyst", "operator", "auditor", "admin"}
    # admin is the only role with the full wildcard.
    assert ROLE_PERMISSIONS["admin"] == frozenset({"*:*"})


def test_every_granted_permission_is_allowed_by_evaluate() -> None:
    """Self-consistency: each concrete grant in the table evaluates to allow.

    Wildcard grants are exercised against a representative concrete resource so a
    table entry that ``evaluate`` would never honor is caught.
    """

    for role, grants in ROLE_PERMISSIONS.items():
        ctx = _ctx(role)
        for grant in grants:
            action, _, resource_type = grant.partition(":")
            probe_action = "DATA_READ" if action == "*" else action
            probe_resource = "metric" if resource_type == "*" else resource_type
            assert evaluate(
                ctx, probe_action, ResourceRef(type=probe_resource)
            ), f"{role} should be allowed {probe_action}:{probe_resource} (grant {grant})"
