"""Security context contracts shared by the platform authz layer.

``tenant_id`` and ``roles`` come only from a verified token -- never from a
request body or model input. RBAC is evaluated as a pure function of the
:class:`SecurityContext`, the action, and the :class:`ResourceRef`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["viewer", "analyst", "operator", "auditor", "admin"]


class Actor(BaseModel):
    type: Literal["user", "service", "system", "copilot"]
    id: str
    roles: list[str] = Field(default_factory=list)


class ResourceRef(BaseModel):
    type: str
    id: str | None = None
    columns: list[str] | None = None


class SecurityContext(BaseModel):
    """The authenticated principal, derived once from the verified JWT."""

    tenant_id: str
    user_id: str
    roles: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    token_id: str | None = None

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes
